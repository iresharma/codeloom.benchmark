from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from . import SYSTEMS, write_json
from .traces import export_run_views

INFRA = {"provider_error", "transport_error", "host_error", "infra_unresolved"}


def load_attempts(root: Path) -> list[dict]:
    rows = []
    for trial in sorted((root / "trials").glob("*")):
        if (trial / "result.json").exists():
            rows.append(json.loads((trial / "result.json").read_text()))
    return rows


def summarize(manifest: dict, attempts: list[dict]) -> dict:
    selected = {}
    costs = defaultdict(float)
    unknown = defaultdict(int)
    executions = defaultdict(list)
    for attempt in attempts:
        if attempt.get("stage") not in ("measured", "recovery"):
            continue
        key = (attempt["task_id"], attempt["system"])
        executions[key].append(attempt)
        if attempt.get("cost_usd") is None or attempt.get("cost_is_conservative"):
            unknown[attempt["system"]] += 1
        else:
            costs[attempt["system"]] += attempt["cost_usd"]
        selected[key] = attempt
    task_rows = []
    pairs = {
        "engine_only": 0,
        "mini_only": 0,
        "both_pass": 0,
        "both_fail": 0,
        "unresolved": 0,
        "unrun": 0,
    }
    for task in manifest["tasks"]:
        task_id = task["id"]
        row = {
            "id": task_id,
            "repo": task.get("repo", task_id.split("-")[0]),
            "category": task.get("category", "unknown"),
            "title": task.get("title", task_id),
            "language": task.get("language", "unknown"),
            "provenance": task.get("provenance", "unknown"),
            "difficulty": task.get("difficulty", "unknown"),
            "attempt_limit_usd": manifest["settings"]
            .get("difficulty_limits_usd", {})
            .get(task.get("difficulty")),
            "systems": {},
        }
        for system in SYSTEMS:
            attempt = selected.get((task_id, system))
            if attempt is None:
                status = "unrun"
            elif attempt["termination"] in INFRA:
                status = "unresolved"
            elif attempt["termination"] == "completed" and attempt.get("verification", {}).get("pass"):
                status = "pass"
            else:
                status = "fail"
            elapsed_ok = attempt and all(
                item.get("elapsed_s") is not None for item in executions[(task_id, system)]
            )
            cost_ok = attempt and not any(
                item.get("cost_usd") is None or item.get("cost_is_conservative")
                for item in executions[(task_id, system)]
            )
            row["systems"][system] = {
                "status": status,
                "termination": attempt.get("termination") if attempt else None,
                "cost_usd": sum(item.get("cost_usd") or 0 for item in executions[(task_id, system)])
                if cost_ok
                else None,
                "elapsed_s": sum(item.get("elapsed_s", 0) for item in executions[(task_id, system)])
                if elapsed_ok
                else None,
                "attempt_id": attempt["id"] if attempt else None,
                "artifact_path": f"trials/{attempt['id']}/" if attempt else None,
                "functional_pass": (attempt.get("functional_verification") or attempt.get("verification") or {}).get(
                    "pass"
                )
                if attempt
                else None,
                "tool_evidence": attempt.get("tool_evidence") if attempt else None,
            }
        left, right = (row["systems"][s]["status"] for s in SYSTEMS)
        if "unrun" in (left, right):
            pairs["unrun"] += 1
        elif "unresolved" in (left, right):
            pairs["unresolved"] += 1
        elif left == right == "pass":
            pairs["both_pass"] += 1
        elif left == right == "fail":
            pairs["both_fail"] += 1
        elif right == "pass":
            pairs["engine_only"] += 1
        else:
            pairs["mini_only"] += 1
        task_rows.append(row)
    systems = []
    for system in SYSTEMS:
        rows = [task["systems"][system] for task in task_rows]
        passed = sum(r["status"] == "pass" for r in rows)
        evaluated = sum(r["status"] in ("pass", "fail") for r in rows)
        durations = [r["elapsed_s"] for r in rows if r["elapsed_s"] is not None]
        systems.append(
            {
                "id": system,
                "passed": passed,
                "evaluated": evaluated,
                "scheduled": len(rows),
                "unresolved": sum(r["status"] == "unresolved" for r in rows),
                "unrun": sum(r["status"] == "unrun" for r in rows),
                "cost_usd": None
                if unknown[system]
                else costs[system]
                if any(key[1] == system for key in executions)
                else None,
                "cost_per_success_usd": costs[system] / passed if passed and not unknown[system] else None,
                "median_elapsed_s": statistics.median(durations) if durations else None,
                "min_elapsed_s": min(durations) if durations else None,
                "max_elapsed_s": max(durations) if durations else None,
                "budget_hits": sum(r["termination"] == "budget_exhausted" for r in rows),
                "unknown_costs": unknown[system],
            }
        )
    executed = sum(s["evaluated"] + s["unresolved"] for s in systems)
    return {
        "schema_version": manifest.get("schema_version", 1),
        "suite": manifest.get("suite", "v1"),
        "run_id": manifest["run_id"],
        "status": "not_run" if not executed else "complete" if not pairs["unrun"] else "partial",
        "model": manifest["settings"]["model"],
        "provider": manifest["settings"]["provider"],
        "reasoning_effort": manifest["settings"].get("reasoning_effort"),
        "thinking_enabled": manifest["settings"].get("thinking_enabled"),
        "classifier": "off",
        "platform": manifest.get("platform", "linux/amd64"),
        "task_count": len(task_rows),
        "difficulty_mix": dict(Counter(t["difficulty"] for t in task_rows)),
        "repo_count": len({t["repo"] for t in task_rows}),
        "budget_usd": 50,
        "pairs": pairs,
        "systems": systems,
        "tasks": task_rows,
        "limitations": manifest.get("limitations", []),
    }


def report(root: Path) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((root / "manifest.json").read_text())
    attempts = load_attempts(root)
    summary = summarize(manifest, attempts)
    write_json(root / "summary.json", summary)
    write_json(root / "site/summary.json", summary)
    with (root / "summary.csv").open("w", newline="") as handle:
        fields = list(summary["systems"][0])
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary["systems"])
    lines = [
        f"# CodeLoom benchmark — {summary['run_id']}",
        "",
        f"Status: {summary['status']}. {summary['task_count']} tasks; no repeated trials or ablations.",
        "",
        f"Generator: {summary['model']} / {summary['provider']}; classifier off.",
        "",
        "| System | Pass / evaluable | Cost | Cost / success |",
        "| --- | --- | --- | --- |",
    ]

    def money(value):
        return "Unavailable" if value is None else f"${value:.4f}"

    for row in summary["systems"]:
        lines.append(
            f"| {row['id']} | {row['passed']} / {row['evaluated']} | {money(row['cost_usd'])} | {money(row['cost_per_success_usd'])} |"
        )
    lines += ["", "Paired outcomes: " + "; ".join(f"{k}: {v}" for k, v in summary["pairs"].items()), ""]
    lines += ["## Limitations", ""] + ["- " + item for item in summary["limitations"]]
    (root / "report.md").write_text("\n".join(lines) + "\n")
    (root / "attempts.jsonl").write_text(
        "".join(json.dumps(attempt, sort_keys=True) + "\n" for attempt in attempts)
    )
    export_run_views(root, summary["run_id"], f"/benchmarks/{summary['run_id']}")
    return summary
