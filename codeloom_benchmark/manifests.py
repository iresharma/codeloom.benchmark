from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

import yaml

from .artifacts import canonical, digest, write_json

ROOT = Path(__file__).resolve().parents[1]


def config():
    return yaml.safe_load((ROOT / "configs/core.yaml").read_text())


def model_metadata():
    record = json.loads((ROOT / "configs/model.json").read_text())
    model = record["model"]
    settings = config()
    if model["id"] != settings["model"]:
        raise ValueError("Model metadata does not match the pinned generator")
    reasoning = model.get("reasoning", {})
    effort = settings["reasoning_effort"]
    supported = reasoning.get("supported_efforts")
    if effort == "none":
        if reasoning.get("mandatory") or settings["thinking_enabled"]:
            raise ValueError("Thinking-disabled configuration is inconsistent")
    elif not settings["thinking_enabled"] or (
        supported is not None and effort not in supported
    ):
        raise ValueError("Unsupported or inconsistent model reasoning effort")
    if not {"tools", "reasoning"}.issubset(model.get("supported_parameters", [])):
        raise ValueError("Pinned model must support reasoning and tools")
    return model


def tasks(suite="v1"):
    path = ROOT / "suites" / suite / "tasks"
    rows = [yaml.safe_load(p.read_text()) for p in sorted(path.glob("*.yaml"))]
    selection_path = ROOT / "suites" / suite / "selection.yaml"
    if not selection_path.exists():
        return rows
    selection = yaml.safe_load(selection_path.read_text())
    selected = selection["tasks"]
    ids = [t["id"] for t in selected]
    available = {t["id"]: t for t in rows}
    if len(set(ids)) != len(ids) or set(ids) - available.keys():
        raise ValueError("Task selection contains duplicate or unknown IDs")
    if Counter(t["difficulty"] for t in selected) != selection["expected_mix"]:
        raise ValueError("Task selection does not match the declared difficulty mix")
    return [
        dict(
            available[t["id"]],
            difficulty=t["difficulty"],
            difficulty_rationale=t["rationale"],
        )
        for t in selected
    ]


def task_limit(task, settings=None):
    settings = settings or config()
    limit = settings["difficulty_limits_usd"][task["difficulty"]]
    if not 0 < limit <= settings["trial_limit_usd"]:
        raise ValueError("Task cap exceeds the frozen maximum")
    return limit


def validate_suite(suite="v1"):
    for setting in ("parallel_attempts", "certification_workers"):
        if config().get(setting, 1) not in (1, 2):
            raise ValueError(f"{setting} must be 1 or 2 for this protocol")
    rows = tasks(suite)
    if suite == "v1":
        for task in rows:
            # Validate each task cap; the ledger enforces shared spending.
            task_limit(task)
    ids = [t["id"] for t in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate task IDs")
    required = {
        "id",
        "repo",
        "category",
        "language",
        "commit",
        "prompt",
        "allowed_paths",
        "verification",
        "provenance",
        "setup",
        "reference",
    }
    for t in rows:
        if required - set(t):
            raise ValueError(f"{t.get('id')}: missing manifest fields")
        if t["category"] not in ("bug", "cross-file", "navigation", "test-generation"):
            raise ValueError("Unknown task category")
        if not isinstance(t["commit"], str) or len(t["commit"]) != 40:
            raise ValueError("Immutable upstream commit required")
        for field in ("setup", "reference"):
            for path in t[field]:
                if path.startswith("/") or ".." in Path(path).parts:
                    raise ValueError("Unsafe patch path")
        if t["category"] == "test-generation" and len(t.get("mutants", [])) < 5:
            raise ValueError("Five held-out mutants required")
    return rows


def fingerprint(suite="v1"):
    paths = [
        ROOT / "configs/core.yaml",
        ROOT / "configs/model.json",
        ROOT / "uv.lock",
        ROOT / "engine-requirements.lock",
    ]
    paths += sorted((ROOT / "suites" / suite).rglob("*.yaml"))
    paths += sorted((ROOT / "suites" / suite / "dependencies").rglob("*.json"))
    paths += sorted((ROOT / "codeloom_benchmark").rglob("*.py"))
    paths += [
        ROOT / "pyproject.toml",
        ROOT / "Dockerfile",
        ROOT / "scripts/check-adapters.py",
    ]
    paths += sorted((ROOT / "runtime-node").glob("*.json"))
    paths += sorted((ROOT / "suites/dev").rglob("*.yaml"))
    paths += sorted((ROOT / "evaluator/dev").rglob("*"))
    paths += sorted((ROOT / "evaluator" / suite).rglob("*"))
    paths += sorted((ROOT / "public").glob("*"))
    paths += sorted((ROOT / "schemas").glob("*.json"))
    table = {str(p.relative_to(ROOT)): digest(p) for p in paths if p.is_file()}
    return hashlib.sha256(canonical(table)).hexdigest(), table


def freeze(suite: str, runtime: dict, schedule: list, calibration: dict):
    rows = validate_suite(suite)
    sha, files = fingerprint(suite)
    record = {
        "suite": suite,
        "status": "frozen",
        "fingerprint": sha,
        "files": files,
        "tasks": [t["id"] for t in rows],
        "runtime": runtime,
        "schedule": schedule,
        "calibration": calibration,
        "settings": config(),
        "limitations": [
            "Author-designed corpus; independent task review unavailable",
            "One run per task; no confidence or reliability claims",
            "Synthetic tasks are labeled; historical contamination is possible",
        ],
    }
    write_json(ROOT / "suites" / suite / "suite.lock.json", record)
    return record


def check_frozen(suite="v1"):
    p = ROOT / "suites" / suite / "suite.lock.json"
    if not p.exists():
        raise ValueError("Suite has not been certified and preregistered")
    lock = json.loads(p.read_text())
    if fingerprint(suite)[0] != lock["fingerprint"]:
        raise ValueError("Suite changed since preregistration")
    runtime = json.loads((ROOT / ".cache/runtime.json").read_text())
    if (
        runtime["image"] != lock["runtime"]["image"]
        or runtime["engine_commit"] != lock["runtime"]["engine_commit"]
    ):
        raise ValueError("Runtime changed since preregistration")
    return lock
