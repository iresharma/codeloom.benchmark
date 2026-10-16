import json
import time
from pathlib import Path

import pytest

from harness import ROOT
from harness.status import discover_runs, log_tail, resolve_run, snapshot, trial_detail


def _task(task_id, difficulty="light", category="bug"):
    return {
        "id": task_id,
        "title": task_id,
        "repo": task_id.split("-")[0],
        "category": category,
        "difficulty": difficulty,
    }


def _manifest(tmp_path, tasks):
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "run_id": tmp_path.name,
                "suite": "v1",
                "settings": {
                    "model": "deepseek/deepseek-v4-pro-0813",
                    "provider": "baidu/fp8",
                },
                "tasks": tasks,
            }
        )
    )


def test_waiting_when_manifest_missing(tmp_path):
    data = snapshot(tmp_path)
    assert data["waiting"]
    assert data["tasks"] == []


def test_live_running_and_finished_arms(tmp_path):
    _manifest(tmp_path, [_task("tinydb-B"), _task("chi-B")])
    mini = tmp_path / "trials/tinydb-B-mini-swe-agent"
    mini.mkdir(parents=True)
    (mini / "attempt.json").write_text(
        json.dumps({"id": "tinydb-B-mini-swe-agent", "started_at": time.time() - 12, "cap_usd": 1})
    )
    (mini / "result.json").write_text(
        json.dumps(
            {
                "id": "tinydb-B-mini-swe-agent",
                "termination": "completed",
                "verification": {"pass": True},
                "cost_usd": 0.02,
                "elapsed_s": 11,
            }
        )
    )
    engine = tmp_path / "trials/tinydb-B-engine-core"
    engine.mkdir(parents=True)
    (engine / "attempt.json").write_text(
        json.dumps({"id": "tinydb-B-engine-core", "started_at": time.time() - 5, "cap_usd": 1})
    )
    (engine / "broker.jsonl").write_text(json.dumps({"status": "complete", "cost_usd": 0.11}) + "\n")
    (engine / "controller.log").write_text("thinking\npatching\n")
    data = snapshot(tmp_path)
    assert data["status"] == "running"
    tinydb = data["tasks"][0]
    assert tinydb["systems"]["mini-swe-agent"]["status"] == "pass"
    assert tinydb["systems"]["engine-core"]["status"] == "running"
    assert tinydb["systems"]["engine-core"]["cost_usd"] == 0.11
    assert data["tasks"][1]["systems"]["mini-swe-agent"]["status"] == "queued"
    assert data["systems"][1]["running"] == 1
    assert log_tail(tmp_path, "tinydb-B-engine-core") == "thinking\npatching"
    assert tinydb["systems"]["mini-swe-agent"]["trace_available"] is False


def test_trial_detail_builds_agent_stack(tmp_path):
    engine = tmp_path / "trials/tinydb-B-engine-core"
    engine.mkdir(parents=True)
    (engine / "engine-events.jsonl").write_text(
        json.dumps(
            {
                "type": "AgentStarted",
                "agent_id": "coder-1",
                "profile": "coder",
                "parent_id": "",
                "task": "fix cache",
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "ToolCallStarted",
                "call_id": "t1",
                "name": "read_file",
                "arguments_json": "{\"path\": \"tinydb/table.py\"}",
                "agent_id": "coder-1",
            }
        )
        + "\n"
        + json.dumps(
            {
                "type": "ToolCallFinished",
                "call_id": "t1",
                "name": "read_file",
                "preview": "class Table",
                "ok": True,
                "duration_ms": 9,
                "agent_id": "coder-1",
            }
        )
        + "\n"
    )
    (engine / "patch.diff").write_text("+self._query_cache.clear()\n")
    view = trial_detail(tmp_path, "tinydb-B-engine-core")
    profiles = {agent["profile"] for agent in view["agents"]}
    assert "coder" in profiles
    tools = [event for event in view["timeline"] if event["kind"] == "tool"]
    assert tools[0]["title"] == "read_file"
    assert tools[0]["ok"] is True
    assert "query_cache" in view["patch"]
    assert "engine-events.jsonl" in view["artifacts"]


def test_snapshot_from_trials_without_manifest(tmp_path):
    trial = tmp_path / "trials/adapter-engine-core"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {
                "id": "adapter-engine-core",
                "task_id": "tinydb-snapshots",
                "system": "engine-core",
                "termination": "completed",
                "verification": {"pass": False},
                "cost_usd": 0,
            }
        )
    )
    data = snapshot(tmp_path)
    assert data["waiting"] is False
    assert data["tasks"][0]["id"] == "adapter"
    assert data["tasks"][0]["systems"]["engine-core"]["status"] == "fail"
    assert data["tasks"][0]["systems"]["engine-core"]["id"] == "adapter-engine-core"


def test_discover_and_resolve_runs():
    runs = discover_runs(ROOT)
    paths = {row["path"] for row in runs}
    assert str((ROOT / "tests/fixtures/monitor-run").resolve()) in paths
    assert str((ROOT / "reports/v2/adapter-check").resolve()) in paths
    opened = resolve_run(ROOT / "reports/v2/adapter-check", [ROOT])
    view = trial_detail(opened, "adapter-engine-core")
    assert any(agent["profile"] == "ask" for agent in view["agents"])
    assert view["requests"]
    with pytest.raises(ValueError):
        resolve_run(Path("/etc/passwd"), [ROOT])
    with pytest.raises(ValueError):
        trial_detail(opened, "../secrets")
