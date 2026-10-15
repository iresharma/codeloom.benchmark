from copy import deepcopy
import json

import pytest
import yaml

from codeloom_benchmark import calibration
from codeloom_benchmark.artifacts import digest


@pytest.fixture
def source(tmp_path, monkeypatch):
    settings = calibration.config()
    original = {
        "status": "passed",
        "runtime_image": "old-runtime",
        "suite_fingerprint": "old-suite",
        "request_timeout_s": 1234,
        "watchdog_s": None,
        "attempts": [
            {
                "task_id": task,
                "system": system,
                "termination": "completed",
                "verification": {"pass": True},
                "cost_is_conservative": False,
            }
            for task in ("dev-python", "dev-typescript")
            for system in settings["systems"]
        ],
    }
    path = tmp_path / "historical.json"
    path.write_text(json.dumps(original))
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache/runtime.json").write_text(json.dumps({"image": "new-runtime"}))
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports/compatibility.json").write_text(
        json.dumps({"status": "passed", "runtime": {"image": "new-runtime"}})
    )
    monkeypatch.setattr(calibration, "ROOT", tmp_path)
    monkeypatch.setattr(calibration, "fingerprint", lambda: ("new-suite", {}))

    def command(args, **kwargs):
        return yaml.safe_dump(settings) if "show" in args else "source-commit"

    monkeypatch.setattr(calibration, "command", command)
    return path, original, settings


def test_reuse_retains_historical_evidence_without_fabricating_current_attempts(source):
    path, original, _ = source
    record = calibration.reuse(
        path, "source", "Owner requested no fresh paid calibration"
    )
    assert calibration.accepted(record)
    assert record["status"] == "reused"
    assert record["attempts"] == []
    assert record["fresh_paid_runs"] is False
    assert record["source_calibration"] == original
    assert record["source_sha256"] == digest(path)
    assert record["source_commit"] == "source-commit"
    assert record["runtime_image"] == "new-runtime"
    assert record["suite_fingerprint"] == "new-suite"
    assert record["request_timeout_s"] == original["request_timeout_s"]
    assert record["watchdog_s"] is None
    assert json.loads(path.read_text()) == original
    assert "has not passed a fresh end-to-end calibration" in record["policy"]


@pytest.mark.parametrize("key", calibration.TIMING_SETTINGS)
def test_changed_timing_settings_require_fresh_calibration(source, monkeypatch, key):
    path, _, settings = source
    changed = deepcopy(settings)
    changed[key] = "changed"
    monkeypatch.setattr(calibration, "config", lambda: changed)
    with pytest.raises(ValueError, match="changed; fresh calibration"):
        calibration.reuse(path, "source", "Owner approved reuse")


@pytest.mark.parametrize(
    "bad", ["status", "attempts", "timeout", "unknown_cost", "failed_check"]
)
def test_failed_or_incomplete_source_cannot_be_reused(source, bad):
    path, original, _ = source
    if bad == "status":
        original["status"] = "failed"
    elif bad == "attempts":
        original["attempts"].pop()
    elif bad == "timeout":
        original["request_timeout_s"] = 0
    elif bad == "unknown_cost":
        original["attempts"][0]["cost_is_conservative"] = True
    else:
        original["attempts"][0]["verification"]["pass"] = False
    path.write_text(json.dumps(original))
    with pytest.raises(ValueError):
        calibration.reuse(path, "source", "Owner approved reuse")


def test_reuse_requires_explicit_reason_and_complete_provenance(source):
    path, _, _ = source
    with pytest.raises(ValueError, match="owner's reason"):
        calibration.reuse(path, "source", " ")
    assert not calibration.accepted({"status": "reused"})
    assert not calibration.accepted({"status": "failed"})
    assert calibration.accepted({"status": "passed"})


def test_reuse_does_not_waive_native_compatibility(source):
    path, _, _ = source
    (path.parent / "reports/compatibility.json").write_text(
        json.dumps({"status": "passed", "runtime": {"image": "stale-runtime"}})
    )
    with pytest.raises(ValueError, match="native compatibility"):
        calibration.reuse(path, "source", "Owner approved reuse")
