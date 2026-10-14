from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path

from .artifacts import checksum_tree, write_json

SYSTEMS = ("mini-swe-agent", "engine-core")
INFRA = {"provider_error", "transport_error", "host_error", "infra_unresolved"}


def unsealed_attempt(trial: Path):
    metadata = json.loads((trial / "attempt.json").read_text())
    return dict(
        metadata,
        termination="infra_unresolved",
        verification={"pass": False, "reason": "unsealed_attempt"},
        cost_usd=None,
        cost_is_conservative=True,
        elapsed_s=None,
        request_count=None,
    )


def load_attempts(root: Path):
    rows = []
    for trial in sorted((root / "trials").glob("*")):
        if (trial / "result.json").exists():
            rows.append(json.loads((trial / "result.json").read_text()))
        elif (trial / "attempt.json").exists():
            rows.append(unsealed_attempt(trial))
    return rows


def summarize(manifest: dict, attempts: list[dict]):
    selected = {}
    costs = defaultdict(float)
    unknown = defaultdict(int)
    executions = defaultdict(list)
    for a in attempts:
        if a.get("stage") not in ("measured", "recovery"):
            continue
        key = (a["task_id"], a["system"])
        executions[key].append(a)
        if a.get("cost_usd") is None or a.get("cost_is_conservative"):
            unknown[a["system"]] += 1
        else:
            costs[a["system"]] += a["cost_usd"]
        if key not in selected or (
            a.get("retry_of") and selected[key].get("termination") in INFRA
        ):
            selected[key] = a
    task_rows = []
    pairs = {
        "engine_only": 0,
        "mini_only": 0,
        "both_pass": 0,
        "both_fail": 0,
        "unresolved": 0,
        "unrun": 0,
    }
    for t in manifest["tasks"]:
        task_id = t["id"] if isinstance(t, dict) else t
        row = {
            "id": task_id,
            "repo": task_id.split("-")[0],
            "category": t.get("category", "unknown")
            if isinstance(t, dict)
            else "unknown",
            "title": t.get("title", task_id) if isinstance(t, dict) else task_id,
            "language": t.get("language", "unknown")
            if isinstance(t, dict)
            else "unknown",
            "provenance": t.get("provenance", "unknown")
            if isinstance(t, dict)
            else "unknown",
            "difficulty": t.get("difficulty", "unknown")
            if isinstance(t, dict)
            else "unknown",
            "attempt_limit_usd": manifest["settings"]
            .get("difficulty_limits_usd", {})
            .get(t.get("difficulty"))
            if isinstance(t, dict)
            else None,
            "systems": {},
        }
        for system in SYSTEMS:
            a = selected.get((task_id, system))
            row["systems"][system] = {
                "status": "unrun"
                if a is None
                else "unresolved"
                if a["termination"] in INFRA
                else "pass"
                if a["termination"] == "completed"
                and a.get("verification", {}).get("pass")
                else "fail",
                "termination": a.get("termination") if a else None,
                "cost_usd": sum(
                    e.get("cost_usd") or 0 for e in executions[(task_id, system)]
                )
                if a
                and not any(
                    e.get("cost_usd") is None or e.get("cost_is_conservative")
                    for e in executions[(task_id, system)]
                )
                else None,
                "elapsed_s": sum(
                    e.get("elapsed_s", 0) for e in executions[(task_id, system)]
                )
                if a
                and all(
                    e.get("elapsed_s") is not None
                    for e in executions[(task_id, system)]
                )
                else None,
                "attempt_id": a["id"] if a else None,
                "artifact_path": f"trials/{a['id']}/" if a else None,
                "regressions": len(a.get("new_failing_check_commands", []))
                if a
                else None,
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
        rows = [t["systems"][system] for t in task_rows]
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
                "cost_per_success_usd": costs[system] / passed
                if passed and not unknown[system]
                else None,
                "median_elapsed_s": statistics.median(durations) if durations else None,
                "min_elapsed_s": min(durations) if durations else None,
                "max_elapsed_s": max(durations) if durations else None,
                "budget_hits": sum(
                    r["termination"] == "budget_exhausted" for r in rows
                ),
                "watchdog_hits": sum(r["termination"] == "watchdog" for r in rows),
                "unknown_costs": unknown[system],
                "regressions": sum(r["regressions"] or 0 for r in rows),
            }
        )
    executed = sum(s["evaluated"] + s["unresolved"] for s in systems)
    return {
        "schema_version": 1,
        "suite": manifest.get("suite", "v1"),
        "run_id": manifest["run_id"],
        "status": "not_run"
        if not executed
        else "complete"
        if not pairs["unrun"]
        else "partial",
        "model": manifest["settings"]["model"],
        "provider": manifest["settings"]["provider"],
        "reasoning_effort": manifest["settings"].get("reasoning_effort"),
        "thinking_enabled": manifest["settings"].get("thinking_enabled"),
        "paid_runs_authorized": manifest["settings"].get("paid_runs_authorized", False),
        "classifier": "off",
        "platform": manifest.get("platform", "linux/amd64"),
        "task_count": len(task_rows),
        "difficulty_mix": dict(
            __import__("collections").Counter(t["difficulty"] for t in task_rows)
        ),
        "repo_count": len({t["repo"] for t in task_rows}),
        "repeat_count": 0,
        "budget_usd": 50,
        "pairs": pairs,
        "systems": systems,
        "tasks": task_rows,
        "limitations": manifest.get("limitations", []),
        "preregistration_commit": manifest.get("preregistration_commit"),
        "calibration_status": manifest.get("calibration_status", "not_run"),
        "blocked_reason": manifest.get("blocked_reason"),
    }


def report(root: Path):
    manifest = json.loads((root / "manifest.json").read_text())
    attempts = load_attempts(root)
    summary = summarize(manifest, attempts)
    write_json(root / "summary.json", summary)
    write_json(root / "site/summary.json", summary)
    with (root / "summary.csv").open("w", newline="") as f:
        fields = list(summary["systems"][0])
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary["systems"])
    lines = [
        f"# CodeLoom benchmark — {summary['run_id']}",
        "",
        f"Status: {summary['status']}. "
        f"{summary['task_count']} tasks; no repeated trials or ablations.",
        "",
        f"Generator: {summary['model']} / {summary['provider']}; classifier off.",
        "",
        "| System | Pass / evaluable | Delivered / scheduled | Cost | Cost / success |",
        "| --- | --- | --- | --- | --- |",
    ]

    def money(x):
        return "Unavailable" if x is None else f"${x:.4f}"

    for row in summary["systems"]:
        lines.append(
            f"| {row['id']} | {row['passed']} / {row['evaluated']} | {row['passed']} / {row['scheduled']} | {money(row['cost_usd'])} | {money(row['cost_per_success_usd'])} |"
        )
    lines += [
        "",
        "Paired outcomes: "
        + "; ".join(f"{k}: {v}" for k, v in summary["pairs"].items()),
        "",
    ]
    if summary.get("blocked_reason"):
        lines += ["Run gate: " + summary["blocked_reason"], ""]
    lines += ["## Observations from traces", ""]
    examples = []
    if summary["status"] == "not_run":
        lines += [
            "No measured traces exist yet. The separately labeled synthetic adapter traces check tool continuation and sealing; they provide no evidence of coding performance.",
            "",
        ]
    else:
        lines += [
            "This is a whole-system comparison with no ablations. These observations describe runs and cannot isolate a component's effect.",
            "",
        ]
        for system in summary["systems"]:
            lines.append(
                f"- {system['id']}: {system['budget_hits']} budget stops, {system['watchdog_hits']} watchdog stops, {system['regressions']} newly failing trusted check commands."
            )
        for system in SYSTEMS:
            for outcome in ("pass", "fail"):
                task = next(
                    (
                        t
                        for t in summary["tasks"]
                        if t["systems"][system]["status"] == outcome
                    ),
                    None,
                )
                if task:
                    arm = task["systems"][system]
                    example = {
                        "task_id": task["id"],
                        "system": system,
                        "outcome": outcome,
                        "attempt_id": arm["attempt_id"],
                        "artifact_path": arm["artifact_path"],
                    }
                    examples.append(example)
                    lines.append(
                        f"- Example {outcome} ({system}, first by task ID): [{task['id']}]({arm['artifact_path']}result.json), termination `{arm['termination']}`. Its directory retains patch, native trace and verification evidence."
                    )
        lines.append("")
    write_json(
        root / "examples.json",
        {
            "selection": "First pass/fail per system by sorted task ID; all attempts remain downloadable",
            "examples": examples,
        },
    )
    lines += ["## Limitations", ""] + ["- " + x for x in summary["limitations"]]
    (root / "report.md").write_text("\n".join(lines) + "\n")
    (root / "attempts.jsonl").write_text(
        "".join(json.dumps(a, sort_keys=True) + "\n" for a in attempts)
    )
    checksum_tree(root)
    return summary
