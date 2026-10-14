from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from .artifacts import write_json
from .evaluate import grade
from .fixtures import capture, command, save_patch, snapshot
from .sandbox import remove, start_container


def journal(root):
    p = root / "broker.jsonl"
    return (
        [json.loads(line) for line in p.read_text().splitlines()] if p.exists() else []
    )


def attempt(
    task,
    system,
    image,
    broker,
    network,
    run_root: Path,
    trial_id,
    stage,
    watchdog_s,
    limit,
    retry_of=None,
):
    root = run_root / "trials" / trial_id
    root.mkdir(parents=True, exist_ok=True)
    token = broker.ledger.create_trial(trial_id, stage, limit)
    cfg = dict(
        broker.settings,
        prompt=task["prompt"],
        root=str(root),
        image=image,
        url=f"http://127.0.0.1:{broker.server.server_address[1]}/v1",
        token=token,
        network=network.name,
        watchdog_s=watchdog_s,
    )
    write_json(
        root / "attempt.json",
        {
            "id": trial_id,
            "task_id": task["id"],
            "system": system,
            "stage": stage,
            "retry_of": retry_of,
            "started_at": time.time(),
        },
    )
    started = time.monotonic()
    container = None
    termination = "agent_crash"
    process = None
    agent_ended = None
    interruption = None
    try:
        if system == "mini-swe-agent":
            argv = [sys.executable, "-m", "codeloom_benchmark.worker"]
        else:
            env = {
                "OPENROUTER_API_KEY": token,
                "OPENROUTER_BASE_URL": network.url,
                "OPENROUTER_MODEL": cfg["model"],
                "OPENROUTER_CHILD_MODEL": cfg["model"],
                "ENGINE_MODEL_CHEAP": cfg["model"],
                "ENGINE_MODEL_STRONG": cfg["model"],
                "ENGINE_REASONING_EFFORT": cfg["reasoning_effort"],
                "ENGINE_JUDGE": "off",
                "ENGINE_INTERVIEW": "0",
                "ENGINE_TRACE_CALLS": "1",
                "ENGINE_LLM_TIMEOUT_S": str(cfg["request_timeout_s"]),
                "ENGINE_LLM_IDLE_S": str(cfg["request_timeout_s"]),
                "ENGINE_EXEC_APPROVAL": "auto",
                "OPENROUTER_DISABLE_RETRIES": "1",
            }
            container = start_container(image, network.name, env=env)
            write_json(root / "container.json", {"id": container})
            argv = [
                "docker",
                "exec",
                "-i",
                container,
                "python",
                "/opt/benchmark-public/engine_driver.py",
            ]
        with (root / "controller.log").open("w") as log:
            process = subprocess.Popen(
                argv,
                stdin=subprocess.PIPE,
                stdout=log,
                stderr=log,
                start_new_session=True,
                text=True,
                cwd=Path(__file__).resolve().parents[1],
                env={
                    **{
                        k: v
                        for k, v in os.environ.items()
                        if k
                        in (
                            "PATH",
                            "LANG",
                            "LC_ALL",
                            "TMPDIR",
                            "DOCKER_HOST",
                            "DOCKER_CONTEXT",
                            "SSL_CERT_FILE",
                        )
                    },
                    "MSWEA_GLOBAL_CONFIG_DIR": str(root / "private-mini-config"),
                    "MSWEA_SILENT_STARTUP": "1",
                    "HOME": str(root / "private-home"),
                },
            )
            try:
                process.communicate(json.dumps(cfg), timeout=watchdog_s)
                if system == "mini-swe-agent":
                    result_path = root / "worker-result.json"
                    result = (
                        json.loads(result_path.read_text())
                        if result_path.exists()
                        else {}
                    )
                    termination = (
                        "completed"
                        if result.get("exit_status") == "Submitted"
                        else "agent_crash"
                    )
                else:
                    termination = (
                        "completed" if process.returncode == 0 else "agent_crash"
                    )
            except subprocess.TimeoutExpired:
                termination = "watchdog"
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        if container is None and (root / "container.json").exists():
            container = json.loads((root / "container.json").read_text())["id"]
        broker.ledger.revoke(trial_id)
        agent_ended = time.monotonic()
        # Stop every process before inspecting output. Restart the sealed container only to export files.
        if container:
            command(["docker", "stop", "--time", "2", container], timeout=30)
            if system == "engine-core":
                for name in ("engine-events.jsonl", "engine-process.log"):
                    subprocess.run(
                        ["docker", "cp", container + ":/tmp/" + name, str(root / name)],
                        capture_output=True,
                    )
            capture(container, root / "checkout")
            with tempfile.TemporaryDirectory() as d:
                base = snapshot(task, Path(d) / "base")
                save_patch(base, root / "checkout", root / "patch.diff")
            verification = grade(
                image, task, root / "checkout", root / "verification.json"
            )
        else:
            verification = {"pass": False, "reason": "no_checkout"}
        statuses = {r["status"] for r in journal(root)}
        if "budget_exhausted" in statuses:
            termination = "budget_exhausted"
        elif statuses & {"model_mismatch", "provider_mismatch"}:
            termination = "protocol_violation"
        elif "request_error" in statuses:
            termination = "agent_crash"
        elif statuses & {"provider_error", "transport_error"}:
            termination = (
                "provider_error" if "provider_error" in statuses else "transport_error"
            )
    except ValueError as exc:
        verification = {"pass": False, "reason": "invalid_output", "detail": str(exc)}
        termination = "invalid_output"
    except Exception as exc:
        verification = {
            "pass": False,
            "reason": "harness_exception",
            "exception": type(exc).__name__,
            "detail": str(exc)[-2000:],
        }
        termination = "host_error"
    except (KeyboardInterrupt, SystemExit) as exc:
        interruption = exc
        termination = "host_error"
        verification = {
            "pass": False,
            "reason": "host_interruption",
            "exception": type(exc).__name__,
        }
    finally:
        broker.ledger.revoke(trial_id)
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        if container is None and (root / "container.json").exists():
            container = json.loads((root / "container.json").read_text())["id"]
        if container:
            remove(container)
    # Logs are published for crashes too, after the attempt token has been revoked.
    for name in ("controller.log", "engine-process.log", "engine-events.jsonl"):
        path = root / name
        if path.exists():
            path.write_text(
                path.read_text(errors="replace").replace(token, "<trial-token>")
            )
    # A disconnected upstream request can still settle. Unknown reservations remain charged and block continuation.
    ledger = broker.ledger.snapshot()
    cost = next(t["cost_usd"] for t in ledger["trials"] if t["id"] == trial_id)
    calls = journal(root)
    from .manifests import ROOT

    certification_path = (
        ROOT / ".cache/certification" / task["id"] / "certification.json"
    )
    certification = (
        json.loads(certification_path.read_text())
        if certification_path.exists()
        else next(
            (
                row
                for row in json.loads(
                    (ROOT / "reports/certification.json").read_text()
                )["tasks"]
                if row["id"] == task["id"]
            ),
            {},
        )
        if (ROOT / "reports/certification.json").exists()
        else {}
    )
    baseline_passed = {
        r["command"]
        for r in certification.get("noop", {}).get("checks", [])
        if r["returncode"] == 0
    }
    failures = {
        r["command"]
        for r in verification.get("checks", [])
        if r["returncode"] and r["command"] in task["verification"]
    }
    result = {
        "id": trial_id,
        "task_id": task["id"],
        "system": system,
        "stage": stage,
        "termination": termination,
        "verification": verification,
        "upstream_check_failures": sorted(failures),
        "new_failing_check_commands": sorted(failures & baseline_passed),
        "cost_usd": cost,
        "cost_is_conservative": any(
            (r.get("usage") or {}).get("cost") is None for r in calls if r.get("id")
        )
        or bool(ledger["unknown_calls"]),
        "elapsed_s": (agent_ended or time.monotonic()) - started,
        "evaluation_elapsed_s": time.monotonic() - agent_ended if agent_ended else None,
        "request_count": sum(bool(r.get("id")) for r in calls),
        "retry_of": retry_of,
    }
    write_json(root / "result.json", result)
    if interruption is not None:
        raise interruption
    return result
