import subprocess

import pytest

from codeloom_benchmark.fixtures import repo_source, snapshot
from codeloom_benchmark.manifests import tasks


@pytest.mark.parametrize(
    "task_id", ["pqueue-B", "pqueue-X", "pqueue-T", "dev-typescript"]
)
def test_pqueue_clock_fix_is_shared_by_starting_and_reference_snapshots(
    tmp_path, task_id
):
    suite = "dev" if task_id.startswith("dev-") else "v1"
    task = next(task for task in tasks(suite) if task["id"] == task_id)
    baseline = snapshot(task, tmp_path / "base")
    reference = snapshot(task, tmp_path / "reference", reference=True)
    original = subprocess.check_output(
        ["git", "show", task["commit"] + ":test/strict.ts"],
        cwd=repo_source(task),
        text=True,
    )
    fixed = (baseline / "test/strict.ts").read_text()
    assert fixed == (reference / "test/strict.ts").read_text()
    before, rest = original.split("test('strict mode multiple complete intervals'", 1)
    _, after = rest.split("test('strict mode precision timing'", 1)
    # All other upstream tests remain byte-identical, and the original bucket
    # assertions are retained inside the one test whose clock is normalized.
    assert fixed.startswith(before)
    assert fixed.endswith("test('strict mode precision timing'" + after)
    assert "interval: 100," in fixed
    assert "intervalCap: 3," in fixed
    assert "t.assert.strictEqual(executionTimes.length, 12);" in fixed
    assert "tasksPerInterval.every(count => count <= 3)" in fixed
