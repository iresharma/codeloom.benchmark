from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import ROOT, read_json, run, write_json
from .docker import Network, export_dir, prepare_runtime, start_container, stop, task_image
from .evidence import grade_evidence
from .grade import grade
from .proxy import Proxy, research_policy
from .settings import (
    MODEL,
    PLATFORM,
    PROVIDER,
    REASONING_EFFORT,
    REQUEST_TIMEOUT_S,
    cap_for,
    schema_version,
)
from .snapshot import save_patch, snapshot
from .submission import copy_tree, pick_submission
from .tasks import load_suite
from .traces import build_view


def trial_id(task, system):
    return f"{task['id']}-{system}"


def _say(message: str) -> None:
    print(f"[harness] {message}", flush=True)


def _engine_env(token, network, task):
    return {
        "OPENROUTER_API_KEY": token,
        "OPENROUTER_BASE_URL": network.url,
        "OPENROUTER_MODEL": MODEL,
        "OPENROUTER_CHILD_MODEL": MODEL,
        "ENGINE_MODEL_CHEAP": MODEL,
        "ENGINE_MODEL_STRONG": MODEL,
        "ENGINE_REASONING_EFFORT": REASONING_EFFORT,
        "ENGINE_JUDGE": "off",
        "ENGINE_INTERVIEW": "0",
        "ENGINE_TRACE_CALLS": "1",
        "ENGINE_LLM_TIMEOUT_S": str(REQUEST_TIMEOUT_S),
        "ENGINE_LLM_IDLE_S": str(REQUEST_TIMEOUT_S),
        "ENGINE_EXEC_APPROVAL": "auto",
        "OPENROUTER_DISABLE_RETRIES": "1",
        "ENGINE_TOOL_IMAGE_MODE": "artifact-only" if task["suite"] == "v2" else "inline",
        "ENGINE_RESEARCH_GATEWAY": network.url.removesuffix("/v1") + "/research",
        "ENGINE_RESEARCH_TOKEN": token,
    }


def _copy_filtered(container: str, dest: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "raw"
        raw.mkdir()
        export_dir(container, "/task/.", raw)
        copy_tree(raw, dest)


def _copy_worktrees(container: str, dest: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "trees"
        if export_dir(container, "/task/.engine/worktrees/.", raw):
            if any(raw.iterdir()):
                shutil.copytree(raw, dest, dirs_exist_ok=True)


def _copy_browser(container: str, dest: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        raw = Path(tmp) / "debug"
        if not export_dir(container, "/task/.engine/debug/.", raw):
            return
        for path in raw.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".jpg", ".png", ".json"}:
                target = dest / path.relative_to(raw)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)


def _journal_status(trial: Path) -> set[str]:
    path = trial / "broker.jsonl"
    if not path.exists():
        return set()
    return {json.loads(line)["status"] for line in path.read_text().splitlines() if line.strip()}


def attempt(task, system, image, proxy: Proxy, network: Network, run_dir: Path) -> dict:
    ident = trial_id(task, system)
    trial = run_dir / "trials" / ident
    if (trial / "result.json").exists():
        _say(f"skip {ident} (already finished)")
        return read_json(trial / "result.json")
    trial.mkdir(parents=True, exist_ok=True)
    cap = cap_for(task["difficulty"])
    _say(f"start {ident}  {task['difficulty']}  cap ${cap:.0f}")
    token = proxy.issue(ident, cap, research_policy(task))
    write_json(
        trial / "attempt.json",
        {
            "id": ident,
            "task_id": task["id"],
            "system": system,
            "stage": "measured",
            "cap_usd": cap,
            "started_at": time.time(),
        },
    )
    container = None
    process = None
    termination = "completed"
    started = time.monotonic()
    try:
        if system == "mini-swe-agent":
            argv = [sys.executable, "-m", "harness.mini"]
            payload = {
                "root": str(trial),
                "image": image,
                "network": network.name,
                "url": network.host_url,
                "research_url": network.url.removesuffix("/v1") + "/research",
                "token": token,
                "model": MODEL,
                "prompt": task["prompt"],
                "reasoning_effort": REASONING_EFFORT,
                "request_timeout_s": REQUEST_TIMEOUT_S,
                "watchdog_s": None,
                "runtime": task.get("runtime") or {},
            }
        else:
            container = start_container(
                image,
                network.name,
                env=_engine_env(token, network, task),
                resources=task.get("runtime"),
            )
            write_json(trial / "container.json", {"id": container})
            argv = ["docker", "exec", "-i", container, "python", "/opt/benchmark-public/engine_session.py"]
            payload = {
                "prompt": task["prompt"],
                "model": MODEL,
                "reasoning_effort": REASONING_EFFORT,
            }
        with (trial / "controller.log").open("w") as log:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                text=True,
                cwd=str(ROOT),
            )
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.close()
            _say(f"agent running {ident}")
            code = process.wait()
        if code not in (0, None):
            termination = "agent_crash"
        elapsed = time.monotonic() - started
        _say(f"agent stopped {ident}  exit {code}  {elapsed:.0f}s")
        if container is None and (trial / "container.json").exists():
            container = read_json(trial / "container.json")["id"]
        if container:
            run(["docker", "stop", "--time", "2", container], timeout=30, check=False)
            if system == "engine-core":
                for name in ("engine-events.jsonl", "engine-process.log"):
                    subprocess.run(
                        ["docker", "cp", container + ":/tmp/" + name, str(trial / name)],
                        capture_output=True,
                    )
            _copy_filtered(container, trial / "checkout")
            _copy_worktrees(container, trial / "worktrees")
            _copy_browser(container, trial / "browser")
            with tempfile.TemporaryDirectory() as tmp:
                baseline = snapshot(task, Path(tmp) / "base")
                submitted, origin = pick_submission(
                    system, baseline, trial / "checkout", trial / "worktrees"
                )
                if origin.startswith("worktree:"):
                    copy_tree(submitted, trial / "checkout")
                save_patch(baseline, submitted, trial / "patch.diff")
                staged = Path(tmp) / "submitted"
                copy_tree(submitted, staged)
                _say(f"grade {ident}  from {origin}")
                verification = grade(image, task, staged, trial / "verification.json")
            view = build_view(run_dir, trial)
            evidence = grade_evidence(task, trial, system, view)
            if task.get("evidence"):
                verification = {
                    **verification,
                    "functional_pass": verification.get("pass"),
                    "tool_evidence": evidence,
                    "pass": bool(verification.get("pass")) and evidence["pass"],
                }
            write_json(trial / "verification.json", verification)
        else:
            origin = "checkout"
            verification = {"pass": False, "reason": "no_checkout"}
        statuses = _journal_status(trial)
        if "budget_exhausted" in statuses:
            termination = "budget_exhausted"
        elif "call_cost_limit" in statuses:
            termination = "call_cost_limit"
        elif statuses & {"provider_error", "transport_error"} and termination == "completed":
            termination = "provider_error" if "provider_error" in statuses else "transport_error"
        result = {
            "id": ident,
            "task_id": task["id"],
            "system": system,
            "stage": "measured",
            "termination": termination,
            "verification": verification,
            "functional_verification": {
                "pass": verification.get("functional_pass", verification.get("pass"))
            },
            "tool_evidence": verification.get("tool_evidence"),
            "cost_usd": proxy.cost(ident),
            "cost_is_conservative": False,
            "elapsed_s": time.monotonic() - started,
            "submission": origin,
        }
    except Exception as exc:
        result = {
            "id": ident,
            "task_id": task["id"],
            "system": system,
            "stage": "measured",
            "termination": "host_error",
            "verification": {"pass": False, "reason": "harness_exception", "detail": str(exc)[-2000:]},
            "cost_usd": proxy.cost(ident),
            "cost_is_conservative": False,
            "elapsed_s": time.monotonic() - started,
        }
    finally:
        proxy.revoke(token)
        if process and process.poll() is None:
            process.kill()
            process.wait()
        if container is None and (trial / "container.json").exists():
            container = read_json(trial / "container.json")["id"]
        if container:
            stop(container)
    write_json(trial / "result.json", result)
    passed = result.get("verification", {}).get("pass")
    _say(
        f"done {ident}  {result.get('termination')}  "
        f"{'pass' if passed else 'fail'}  ${result.get('cost_usd') or 0:.3f}  "
        f"{result.get('elapsed_s') or 0:.0f}s"
    )
    return result


def measured(name: str, run_dir: Path, engine: Path, only=None, parallel=2):
    from .report import report
    from .settings import LIMITATIONS, suite_id

    suite = suite_id(name)
    tasks = load_suite(name, only)
    run_dir.mkdir(parents=True, exist_ok=True)
    _say(f"campaign {run_dir.name}  suite {name} ({suite})  {len(tasks)} tasks  parallel {parallel}")
    write_json(
        run_dir / "manifest.json",
        {
            "run_id": run_dir.name,
            "suite": suite,
            "schema_version": schema_version(suite),
            "platform": PLATFORM,
            "limitations": LIMITATIONS[suite],
            "settings": {
                "model": MODEL,
                "provider": PROVIDER,
                "reasoning_effort": REASONING_EFFORT,
                "thinking_enabled": True,
                "difficulty_limits_usd": {"light": 1, "medium": 2, "heavy": 4},
                "paid_runs_authorized": True,
                "budget_policy": "actual-spend",
                "call_limit_multiplier": 3,
            },
            "tasks": tasks,
        },
    )
    if not (ROOT / ".cache/runtime.json").exists():
        _say("runtime image missing; building")
        prepare_runtime(engine)
    _say("preparing task images")
    images = {task["id"]: task_image(task) for task in tasks}
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("Set OPENROUTER_API_KEY before a paid run")
    proxy = Proxy(key, run_dir)
    port = proxy.start()
    network = Network(port)
    _say(f"proxy on :{port}  network {network.name}")
    try:
        workers = 2 if parallel != 1 else 1
        for index, task in enumerate(tasks, 1):
            _say(f"task {index}/{len(tasks)}  {task['id']}  {task['difficulty']}  {task['title']}")
            pending = [
                (task, system, images[task["id"]])
                for system in ("mini-swe-agent", "engine-core")
                if not (run_dir / "trials" / trial_id(task, system) / "result.json").exists()
            ]
            if not pending:
                _say(f"skip {task['id']} (both arms finished)")
                continue
            if workers == 1:
                for item in pending:
                    attempt(*item, proxy, network, run_dir)
            else:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    futures = [
                        pool.submit(attempt, *item, proxy, network, run_dir) for item in pending
                    ]
                    for future in as_completed(futures):
                        future.result()
            report(run_dir)
    finally:
        network.close()
        proxy.close()
    summary = report(run_dir)
    spend = sum(s.get("cost_usd") or 0 for s in summary.get("systems", []))
    _say(f"campaign {summary.get('status')}  spend ${spend:.3f}")
    return summary
