"""Live snapshot of a run directory. Used by the local monitor."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from . import ROOT, SYSTEMS, read_json
from .report import INFRA
from .settings import cap_for
from .traces import build_view


def _broker(trial: Path) -> tuple[float, int]:
    path = trial / "broker.jsonl"
    if not path.exists():
        return 0.0, 0
    spend, count = 0.0, 0
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        count += 1
        spend += float(row.get("cost_usd") or 0)
    return spend, count


def _arm(task: dict, system: str, run_dir: Path, now: float) -> dict:
    ident = f"{task['id']}-{system}"
    trial = run_dir / "trials" / ident
    result = read_json(trial / "result.json")
    attempt = read_json(trial / "attempt.json")
    spend, requests = _broker(trial)
    if result:
        passed = result.get("termination") == "completed" and result.get("verification", {}).get("pass")
        if result.get("termination") in INFRA:
            status = "unresolved"
        elif passed:
            status = "pass"
        else:
            status = "fail"
        return {
            "id": ident,
            "status": status,
            "termination": result.get("termination"),
            "cost_usd": result.get("cost_usd") if result.get("cost_usd") is not None else spend,
            "elapsed_s": result.get("elapsed_s"),
            "requests": requests,
            "log_available": (trial / "controller.log").exists(),
            "trace_available": _trace_available(trial),
        }
    if attempt:
        started = float(attempt.get("started_at") or (trial / "attempt.json").stat().st_mtime)
        return {
            "id": ident,
            "status": "running",
            "termination": None,
            "cost_usd": spend,
            "elapsed_s": max(0.0, now - started),
            "requests": requests,
            "log_available": (trial / "controller.log").exists(),
            "trace_available": _trace_available(trial),
        }
    return {
        "id": ident,
        "status": "queued",
        "termination": None,
        "cost_usd": None,
        "elapsed_s": None,
        "requests": 0,
        "log_available": False,
        "trace_available": False,
    }


def _trace_available(trial: Path) -> bool:
    return (trial / "engine-events.jsonl").exists() or (trial / "native-trace.json").exists()


def snapshot(run_dir: Path, now=None) -> dict:
    now = time.time() if now is None else now
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.exists():
        synthesized = _tasks_from_trials(run_dir)
        if not synthesized:
            return {
                "waiting": True,
                "run_dir": str(run_dir),
                "updated_at": now,
                "tasks": [],
                "systems": [],
            }
        manifest = {"run_id": run_dir.name, "suite": None, "settings": {}, "tasks": synthesized}
    else:
        manifest = json.loads(manifest_path.read_text())
    tasks = []
    listed = manifest.get("tasks") or _tasks_from_trials(run_dir)
    for task in listed:
        row = {
            "id": task["id"],
            "title": task.get("title", task["id"]),
            "repo": task.get("repo", task["id"].split("-")[0]),
            "category": task.get("category", ""),
            "difficulty": task.get("difficulty", ""),
            "cap_usd": cap_for(task["difficulty"]) if task.get("difficulty") in ("light", "medium", "heavy") else None,
            "systems": {system: _arm(task, system, run_dir, now) for system in SYSTEMS},
        }
        tasks.append(row)

    def rollup(system):
        arms = [task["systems"][system] for task in tasks]
        costs = [a["cost_usd"] for a in arms if a["cost_usd"] is not None]
        return {
            "id": system,
            "passed": sum(a["status"] == "pass" for a in arms),
            "failed": sum(a["status"] == "fail" for a in arms),
            "running": sum(a["status"] == "running" for a in arms),
            "queued": sum(a["status"] == "queued" for a in arms),
            "scheduled": len(arms),
            "cost_usd": sum(costs) if costs else 0.0,
        }

    systems = [rollup(system) for system in SYSTEMS]
    running = sum(s["running"] for s in systems)
    finished = sum(s["passed"] + s["failed"] for s in systems)
    scheduled = sum(s["scheduled"] for s in systems)
    if not scheduled:
        status = "waiting"
    elif running:
        status = "running"
    elif finished == scheduled:
        status = "complete"
    elif finished:
        status = "partial"
    else:
        status = "queued"
    pairs = {"both_pass": 0, "both_fail": 0, "engine_only": 0, "mini_only": 0, "open": 0}
    for task in tasks:
        left = task["systems"]["mini-swe-agent"]["status"]
        right = task["systems"]["engine-core"]["status"]
        if left in ("queued", "running") or right in ("queued", "running"):
            pairs["open"] += 1
        elif left == right == "pass":
            pairs["both_pass"] += 1
        elif left == right == "fail":
            pairs["both_fail"] += 1
        elif right == "pass":
            pairs["engine_only"] += 1
        else:
            pairs["mini_only"] += 1
    settings = manifest.get("settings") or {}
    return {
        "waiting": False,
        "run_dir": str(run_dir),
        "run_id": manifest.get("run_id", run_dir.name),
        "suite": manifest.get("suite"),
        "status": status,
        "model": settings.get("model"),
        "provider": settings.get("provider"),
        "updated_at": now,
        "spend_usd": sum(s["cost_usd"] for s in systems),
        "pairs": pairs,
        "systems": systems,
        "tasks": tasks,
    }


def log_tail(run_dir: Path, trial_id: str, lines=80) -> str:
    trial = _trial_dir(run_dir, trial_id)
    path = trial / "controller.log"
    if not path.exists():
        return ""
    text = path.read_text(errors="replace")
    if "sk-or-v1-" in text:
        text = text.replace("sk-or-v1-", "<redacted>-")
    body = text.splitlines()[-lines:]
    return "\n".join(body)


def _trial_dir(run_dir: Path, trial_id: str) -> Path:
    if not trial_id or "/" in trial_id or ".." in trial_id:
        raise ValueError("Invalid trial id")
    root = (run_dir / "trials").resolve()
    trial = (root / trial_id).resolve()
    if trial.parent != root or not trial.is_dir():
        raise ValueError("Invalid trial id")
    return trial


def _tasks_from_trials(run_dir: Path) -> list[dict]:
    folder = run_dir / "trials"
    if not folder.is_dir():
        return []
    tasks = {}
    for path in sorted(folder.iterdir()):
        if not path.is_dir():
            continue
        for system in SYSTEMS:
            suffix = "-" + system
            if path.name.endswith(suffix):
                ident = path.name[: -len(suffix)]
                result = read_json(path / "result.json") or {}
                attempt = read_json(path / "attempt.json") or {}
                tasks.setdefault(
                    ident,
                    {
                        "id": ident,
                        "title": result.get("task_id") or attempt.get("task_id") or ident,
                        "repo": ident.split("-")[0],
                        "category": "",
                        "difficulty": "",
                    },
                )
                break
    return list(tasks.values())


def pull_live_traces(trial: Path) -> None:
    meta = read_json(trial / "container.json") or {}
    cid = meta.get("id")
    if not cid:
        return
    for name in ("engine-events.jsonl", "engine-process.log"):
        try:
            subprocess.run(
                ["docker", "cp", f"{cid}:/tmp/{name}", str(trial / name)],
                capture_output=True,
                timeout=8,
                check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return


def trial_detail(run_dir: Path, trial_id: str) -> dict:
    trial = _trial_dir(run_dir, trial_id)
    pull_live_traces(trial)
    view = build_view(run_dir, trial)
    view["log"] = log_tail(run_dir, trial_id, lines=200)
    view["artifacts"] = sorted(path.name for path in trial.iterdir() if path.is_file())
    if (trial / "result.json").exists():
        if view.get("termination") in INFRA:
            view["status"] = "unresolved"
        elif view.get("passed"):
            view["status"] = "pass"
        else:
            view["status"] = "fail"
    elif (trial / "attempt.json").exists():
        view["status"] = "running"
    else:
        view["status"] = "queued"
    return view


def _run_card(path: Path) -> dict:
    manifest = read_json(path / "manifest.json") or {}
    trial_root = path / "trials"
    trials = [p for p in trial_root.iterdir() if p.is_dir()] if trial_root.is_dir() else []
    finished = sum(1 for trial in trials if (trial / "result.json").exists())
    running = sum(
        1 for trial in trials if (trial / "attempt.json").exists() and not (trial / "result.json").exists()
    )
    if running:
        status = "running"
    elif trials and finished == len(trials):
        status = "complete"
    elif finished:
        status = "partial"
    elif manifest:
        status = "queued"
    else:
        status = "idle"
    mtimes = [path.stat().st_mtime, *(trial.stat().st_mtime for trial in trials[:80])]
    return {
        "path": str(path.resolve()),
        "run_id": manifest.get("run_id", path.name),
        "suite": manifest.get("suite"),
        "status": status,
        "trials": len(trials),
        "finished": finished,
        "running": running,
        "updated_at": max(mtimes) if mtimes else 0,
    }


def discover_runs(root: Path = ROOT, extra: Path | None = None) -> list[dict]:
    bases = [root / "runs", root / "reports", root / "tests" / "fixtures"]
    seen = set()
    found = []
    candidates = []
    skip_roots = {base.resolve() for base in bases if base.exists()}
    if extra:
        candidates.append(extra.resolve())
    for base in bases:
        if not base.exists():
            continue
        candidates.extend(base.glob("*"))
        candidates.extend(base.glob("*/*"))
    for path in candidates:
        if not path.is_dir():
            continue
        resolved = path.resolve()
        key = str(resolved)
        if key in seen or resolved in skip_roots:
            continue
        if not (path / "manifest.json").exists() and not (path / "trials").is_dir():
            continue
        seen.add(key)
        found.append(_run_card(path))
    found.sort(key=lambda row: (row["running"], row["finished"], row["updated_at"]), reverse=True)
    return found


def resolve_run(path: Path, allowed: list[Path]) -> Path:
    resolved = path.expanduser().resolve()
    for root in allowed:
        try:
            resolved.relative_to(root.resolve())
            break
        except ValueError:
            continue
    else:
        raise ValueError("Run is outside allowed directories")
    if not (resolved / "manifest.json").exists() and not (resolved / "trials").is_dir():
        raise ValueError("Not a run directory")
    return resolved
