import json

from jsonschema import validate

from harness import ROOT
from harness.report import report, summarize


def _manifest(tasks):
    return {
        "run_id": "classic-local",
        "suite": "v1",
        "schema_version": 1,
        "platform": "linux/amd64",
        "limitations": ["Author-designed corpus; no independent review."],
        "settings": {
            "model": "deepseek/deepseek-v4-pro-0813",
            "provider": "baidu/fp8",
            "reasoning_effort": "high",
            "thinking_enabled": True,
            "difficulty_limits_usd": {"light": 1, "medium": 2, "heavy": 4},
        },
        "tasks": tasks,
    }


def test_empty_run_matches_site_schema(tmp_path):
    tasks = [
        {
            "id": "tinydb-B",
            "repo": "tinydb",
            "category": "bug",
            "title": "Query cache survives writes",
            "language": "python",
            "difficulty": "light",
        }
    ]
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "manifest.json").write_text(json.dumps(_manifest(tasks)))
    (tmp_path / "trials").mkdir()
    summary = report(tmp_path)
    schema = json.loads((ROOT / "schemas/site-summary.json").read_text())
    validate(summary, schema)
    assert summary["status"] == "not_run"
    assert summary["pairs"]["unrun"] == 1


def test_paired_pass_counts():
    tasks = [
        {
            "id": "tinydb-B",
            "repo": "tinydb",
            "category": "bug",
            "title": "x",
            "language": "python",
            "difficulty": "light",
        }
    ]
    attempts = [
        {
            "id": "tinydb-B-mini-swe-agent",
            "task_id": "tinydb-B",
            "system": "mini-swe-agent",
            "stage": "measured",
            "termination": "completed",
            "verification": {"pass": True},
            "cost_usd": 0.02,
            "elapsed_s": 12,
        },
        {
            "id": "tinydb-B-engine-core",
            "task_id": "tinydb-B",
            "system": "engine-core",
            "stage": "measured",
            "termination": "completed",
            "verification": {"pass": True},
            "cost_usd": 0.2,
            "elapsed_s": 40,
        },
    ]
    summary = summarize(_manifest(tasks), attempts)
    assert summary["pairs"]["both_pass"] == 1
    assert summary["systems"][0]["passed"] == 1
    assert summary["status"] == "complete"
