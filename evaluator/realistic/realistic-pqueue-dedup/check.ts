import assert from 'node:assert/strict';
import PQueue from './source/index.ts';

// A task with an `id` already pending returns the SAME promise, and the new function is never called.
{
	const calls: string[] = [];
	const queue = new PQueue({concurrency: 1});

	const first = queue.add(async () => {
		calls.push('first');
		return 'first-result';
	}, {id: 'k'});

	const second = queue.add(async () => {
		calls.push('second');
		return 'second-result';
	}, {id: 'k'});

	assert.equal(first, second, 'duplicate add() must return the exact same promise');
	assert.equal(queue.size, 0, 'the duplicate must not be enqueued');
	assert.equal(queue.pending, 1);

	assert.equal(await first, 'first-result');
	assert.equal(await second, 'first-result');
	assert.deepEqual(calls, ['first'], 'the duplicate function must never run');
}

// A task with an `id` already waiting (not yet running, because concurrency is saturated) is also deduplicated.
{
	const calls: string[] = [];
	const queue = new PQueue({concurrency: 1});

	const blocker = queue.add(async () => {
		await new Promise(resolve => {
			setTimeout(resolve, 20);
		});
	});

	const waiting = queue.add(async () => {
		calls.push('waiting');
		return 'waiting-result';
	}, {id: 'k2'});

	assert.equal(queue.size, 1);

	const duplicate = queue.add(async () => {
		calls.push('duplicate');
		return 'duplicate-result';
	}, {id: 'k2'});

	assert.equal(queue.size, 1, 'duplicate must not add a second waiting task');
	assert.equal(waiting, duplicate);

	await blocker;
	assert.equal(await duplicate, 'waiting-result');
	assert.deepEqual(calls, ['waiting']);
}

// Once the original task settles, a later add() with the same id enqueues a fresh task and runs it.
{
	const calls: string[] = [];
	const queue = new PQueue({concurrency: 1});

	const first = queue.add(async () => {
		calls.push('first');
		return 'first-result';
	}, {id: 'k3'});

	assert.equal(await first, 'first-result');

	const second = queue.add(async () => {
		calls.push('second');
		return 'second-result';
	}, {id: 'k3'});

	assert.notEqual(first, second, 'a fresh task must be a new promise');
	assert.equal(await second, 'second-result');
	assert.deepEqual(calls, ['first', 'second']);
}

// Dedup also works through addAll(): a task sharing an id with an already-pending task is skipped.
{
	const calls: string[] = [];
	const queue = new PQueue({concurrency: 1});

	const blocker = queue.add(async () => {
		await new Promise(resolve => {
			setTimeout(resolve, 20);
		});
	});

	const first = queue.add(async () => {
		calls.push('first');
		return 'first-result';
	}, {id: 'shared'});

	const [second] = await queue.addAll([
		async () => {
			calls.push('second');
			return 'second-result';
		},
	], {id: 'shared'});

	await blocker;
	await first;

	assert.equal(second, 'first-result', 'addAll() must reuse the pending task with the same id');
	assert.deepEqual(calls, ['first'], 'the addAll() duplicate function must never run');
}

// Two different ids never collide: both run independently and concurrently.
{
	const calls: string[] = [];
	const queue = new PQueue({concurrency: 2});

	const a = queue.add(async () => {
		calls.push('a');
		return 'a-result';
	}, {id: 'id-a'});
	const b = queue.add(async () => {
		calls.push('b');
		return 'b-result';
	}, {id: 'id-b'});

	assert.equal(await a, 'a-result');
	assert.equal(await b, 'b-result');
	assert.deepEqual(calls.sort(), ['a', 'b']);
}

console.log('OK');
