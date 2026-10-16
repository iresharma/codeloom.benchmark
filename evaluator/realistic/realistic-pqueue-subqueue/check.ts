import assert from 'node:assert/strict';
import PQueue from './source/index.ts';

// Two tasks in the same group, groupConcurrency 1: only one runs at a time, even with global concurrency wide open.
{
	const order: string[] = [];
	const queue = new PQueue({concurrency: 10, groupConcurrency: {slow: 1}});

	let activeInGroup = 0;
	let maxActiveInGroup = 0;

	const task = async (name: string) => {
		activeInGroup++;
		maxActiveInGroup = Math.max(maxActiveInGroup, activeInGroup);
		order.push(`start:${name}`);
		await new Promise(resolve => {
			setTimeout(resolve, 20);
		});
		order.push(`end:${name}`);
		activeInGroup--;
	};

	await Promise.all([
		queue.add(() => task('a'), {group: 'slow'}),
		queue.add(() => task('b'), {group: 'slow'}),
		queue.add(() => task('c'), {group: 'slow'}),
	]);

	assert.equal(maxActiveInGroup, 1, 'group concurrency of 1 must never let two tasks run at once');
	assert.deepEqual(order, ['start:a', 'end:a', 'start:b', 'end:b', 'start:c', 'end:c']);
}

// A task with no group is unaffected by another group's limit, and runs concurrently with it.
{
	const queue = new PQueue({concurrency: 10, groupConcurrency: {slow: 1}});
	let slowRunning = false;
	let ungatedRanWhileSlowRunning = false;

	const slow = queue.add(async () => {
		slowRunning = true;
		await new Promise(resolve => {
			setTimeout(resolve, 30);
		});
		slowRunning = false;
	}, {group: 'slow'});

	await new Promise(resolve => {
		setTimeout(resolve, 5);
	});

	await queue.add(async () => {
		ungatedRanWhileSlowRunning = slowRunning;
	});

	assert.equal(ungatedRanWhileSlowRunning, true, 'an ungated task must not wait on an unrelated group');
	await slow;
}

// Two different groups, each with their own limit, run independently of each other.
{
	const queue = new PQueue({concurrency: 10, groupConcurrency: {a: 1, b: 1}});
	let aActive = 0;
	let bActive = 0;
	let maxAActive = 0;
	let maxBActive = 0;
	let sawBothActiveAtOnce = false;

	const makeTask = (group: 'a' | 'b') => async () => {
		if (group === 'a') {
			aActive++;
			maxAActive = Math.max(maxAActive, aActive);
		} else {
			bActive++;
			maxBActive = Math.max(maxBActive, bActive);
		}

		if (aActive > 0 && bActive > 0) {
			sawBothActiveAtOnce = true;
		}

		await new Promise(resolve => {
			setTimeout(resolve, 15);
		});

		if (group === 'a') {
			aActive--;
		} else {
			bActive--;
		}
	};

	await Promise.all([
		queue.add(makeTask('a'), {group: 'a'}),
		queue.add(makeTask('a'), {group: 'a'}),
		queue.add(makeTask('b'), {group: 'b'}),
		queue.add(makeTask('b'), {group: 'b'}),
	]);

	assert.equal(maxAActive, 1, 'group a must respect its own limit of 1');
	assert.equal(maxBActive, 1, 'group b must respect its own limit of 1');
	assert.equal(sawBothActiveAtOnce, true, 'group a and group b must run independently, not serialized against each other');
}

// setGroupConcurrency() raises the limit at runtime and wakes waiters.
{
	const queue = new PQueue({concurrency: 10, groupConcurrency: {g: 1}});
	let active = 0;
	let maxActive = 0;

	const task = async () => {
		active++;
		maxActive = Math.max(maxActive, active);
		await new Promise(resolve => {
			setTimeout(resolve, 20);
		});
		active--;
	};

	const first = queue.add(task, {group: 'g'});
	const second = queue.add(task, {group: 'g'});

	await new Promise(resolve => {
		setTimeout(resolve, 5);
	});

	queue.setGroupConcurrency('g', 2);

	await Promise.all([first, second]);
	assert.equal(maxActive, 2, 'raising the group limit at runtime must let a waiting task start');
}

console.log('all pqueue-subqueue checks passed');
