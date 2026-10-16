from __future__ import annotations

import yaml

from . import ROOT
from .settings import suite_id


def load_suite(name: str, only=None) -> list[dict]:
    suite = suite_id(name)
    available = {}
    for path in sorted((ROOT / "suites" / suite / "tasks").glob("*.yaml")):
        row = yaml.safe_load(path.read_text())
        row["suite"] = suite
        available[row["id"]] = row
    selected = yaml.safe_load((ROOT / "suites" / suite / "selection.yaml").read_text())
    tasks = []
    for item in selected["tasks"]:
        task = dict(available[item["id"]])
        task["difficulty"] = item["difficulty"]
        task["difficulty_rationale"] = item.get("rationale", "")
        tasks.append(task)
    if only:
        wanted = set(only)
        missing = wanted - {t["id"] for t in tasks}
        if missing:
            raise ValueError("Unknown task ids: " + ", ".join(sorted(missing)))
        tasks = [t for t in tasks if t["id"] in wanted]
    return tasks


def oracle_dir(task: dict):
    return ROOT / "evaluator" / task["suite"] / task["id"]
