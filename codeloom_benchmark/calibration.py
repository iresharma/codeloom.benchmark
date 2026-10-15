"""Explicit historical timing reuse, without claiming fresh runtime validation."""

import json
import math
from pathlib import Path

import yaml

from .artifacts import digest, write_json
from .fixtures import command
from .manifests import ROOT, config, fingerprint

TIMING_SETTINGS = (
    "model",
    "provider",
    "reasoning_effort",
    "thinking_enabled",
    "max_tokens",
    "parallel_attempts",
    "budget_policy",
    "call_limit_multiplier",
    "watchdog_s",
)


def accepted(record):
    if record.get("status") == "passed":
        return True
    return (
        record.get("status") == "reused"
        and record.get("fresh_paid_runs") is False
        and bool(str(record.get("owner_reason", "")).strip())
        and record.get("source_calibration", {}).get("status") == "passed"
        and bool(record.get("source_commit"))
        and bool(record.get("source_sha256"))
    )


def reuse(source: Path, source_commit: str, owner_reason: str):
    if not owner_reason.strip():
        raise ValueError("Historical timing reuse requires the owner's reason")
    original = json.loads(source.read_text())
    attempts = original.get("attempts", [])
    expected = {
        (task, system)
        for task in ("dev-python", "dev-typescript")
        for system in config()["systems"]
    }
    if (
        original.get("status") != "passed"
        or len(attempts) != 4
        or {(row.get("task_id"), row.get("system")) for row in attempts} != expected
        or any(
            row.get("termination") != "completed"
            or row.get("verification", {}).get("pass") is not True
            or row.get("cost_is_conservative") is not False
            for row in attempts
        )
    ):
        raise ValueError("Reuse requires a completed, passing four-arm calibration")
    timeout = original.get("request_timeout_s")
    if (
        not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("Source calibration has no valid request timeout")
    git = ["git", f"--git-dir={ROOT / '.git'}", f"--work-tree={ROOT}"]
    commit = command([*git, "rev-parse", source_commit + "^{commit}"], cwd=ROOT)
    old_settings = yaml.safe_load(
        command([*git, "show", commit + ":configs/core.yaml"], cwd=ROOT)
    )
    settings = config()
    if any(old_settings.get(key) != settings.get(key) for key in TIMING_SETTINGS):
        raise ValueError(
            "Model, provider or timing policy changed; fresh calibration is required"
        )
    runtime = json.loads((ROOT / ".cache/runtime.json").read_text())
    record = {
        "status": "reused",
        "fresh_paid_runs": False,
        "compatibility_policy": "previous_tests_accepted",
        "attempts": [],
        "owner_reason": owner_reason.strip(),
        "source_commit": commit,
        "source_sha256": digest(source),
        "source_calibration": original,
        "source_settings": {key: old_settings.get(key) for key in TIMING_SETTINGS},
        "suite_fingerprint": fingerprint()[0],
        "runtime_image": runtime["image"],
        "request_timeout_s": timeout,
        "watchdog_s": settings.get("watchdog_s"),
        "policy": "Owner waived fresh paid calibration and redundant compatibility probes. Historical request timing and existing adapter tests are accepted; the current runtime has not passed a fresh end-to-end calibration. Fixture certification remains required.",
    }
    write_json(ROOT / "reports/calibration.json", record)
    return record
