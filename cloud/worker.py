"""Detached Railway supervisor. Fixed protocol gates; safe public projections."""

from __future__ import annotations
import fcntl
import argparse
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from codeloom_benchmark.cli import draft, import_site  # noqa: E402
from codeloom_benchmark.manifests import config  # noqa: E402
from codeloom_benchmark.ledger import Ledger  # noqa: E402

RUN = ROOT / "runs/railway-v1"
CALIBRATION = ROOT / "runs/railway-calibration"
STATE = ROOT / ".cache/cloud"
URL = os.environ["MONITOR_URL"].rstrip("/")
TOKEN = os.environ["MONITOR_UPLOAD_TOKEN"]
state = {
    "phase": "starting",
    "message": "Preparing the fixed protocol",
    "started_at": time.time(),
    "log": [],
}
state_lock = threading.Lock()
stop = threading.Event()


def read(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def upload(kind, data):
    request = urllib.request.Request(
        URL + "/api/upload/" + kind,
        data=data,
        headers={
            "Authorization": "Bearer " + TOKEN,
            "Content-Type": "application/octet-stream",
        },
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        if response.status != 200:
            raise RuntimeError("Monitor refused upload")


def archive(folder):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as tar:
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                tar.add(p, arcname=p.relative_to(folder), recursive=False)
    return output.getvalue()


def backup():
    with tempfile.TemporaryDirectory() as folder:
        stage = Path(folder)
        ledger = ROOT / ".cache/campaign/ledger.sqlite"
        if ledger.exists():
            dest = stage / ".cache/campaign/ledger.sqlite"
            dest.parent.mkdir(parents=True)
            with sqlite3.connect(ledger) as source, sqlite3.connect(dest) as target:
                source.backup(target)
        import shutil

        for rel in (
            "runs",
            "reports",
            "suites/v1/suite.lock.json",
            ".cache/runtime.json",
            ".cache/cloud/state.json",
        ):
            source = ROOT / rel
            if source.is_dir():
                shutil.copytree(source, stage / rel)
            elif source.is_file():
                target = stage / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
        upload("backup", archive(stage))


def publish():
    import shutil

    with tempfile.TemporaryDirectory() as folder:
        stage = Path(folder)
        pages = stage / "pages"

        def visible_input(source):
            # Running attempts are status only, never counted as interrupted outcomes.
            target = stage / "inputs" / source.name
            target.mkdir(parents=True)
            shutil.copy2(source / "manifest.json", target / "manifest.json")
            terminal = state["phase"] in ("complete", "blocked")
            for trial in (source / "trials").glob("*"):
                if (trial / "result.json").exists() or terminal:
                    shutil.copytree(trial, target / "trials" / trial.name)
            if (source / "blobs").exists():
                shutil.copytree(source / "blobs", target / "blobs")
            return target

        summary = import_site(visible_input(RUN), pages)
        measured_runs = read(
            pages / "packages/ui/src/landings/engine/results/data/run-index.json", []
        )
        if (CALIBRATION / "manifest.json").exists():
            import_site(visible_input(CALIBRATION), pages)
            dev_runs = read(
                pages / "packages/ui/src/landings/engine/results/data/run-index.json",
                [],
            )
        else:
            dev_runs = []
        out = stage / "public"
        out.mkdir()
        shutil.copytree(pages / "apps/web/public/benchmarks", out / "benchmarks")
        (out / "summary.json").write_text(json.dumps(summary))
        setup = read(out / "benchmarks" / RUN.name / "setup-summary.json", {})
        setup["published_attempts"] = sum(
            not r["synthetic"] for r in measured_runs + dev_runs
        )
        (out / "setup.json").write_text(json.dumps(setup))
        unique = {(r["run_id"], r["id"]): r for r in measured_runs + dev_runs}
        (out / "runs.json").write_text(json.dumps(list(unique.values())))
        upload("public", archive(out))


def heartbeat():
    previous = None
    last_backup = 0
    while not stop.is_set():
        try:
            active = []
            for root in (CALIBRATION, RUN):
                for trial in sorted((root / "trials").glob("*")):
                    if not (trial / "result.json").exists():
                        row = read(trial / "attempt.json", {})
                        if row:
                            active.append(
                                {
                                    k: row.get(k)
                                    for k in (
                                        "id",
                                        "task_id",
                                        "system",
                                        "stage",
                                        "started_at",
                                    )
                                }
                            )
                            # Only counts are live; opaque tokens and raw prompts stay private.
                            journal = trial / "broker.jsonl"
                            active[-1]["requests"] = (
                                len(journal.read_text().splitlines())
                                if journal.exists()
                                else 0
                            )
            with state_lock:
                snapshot = dict(state, heartbeat_at=time.time(), active=active)
            ledger = ROOT / ".cache/campaign/ledger.sqlite"
            if ledger.exists():
                settings = config()
                snapshot["budget"] = Ledger(
                    ledger, settings["campaign_limit_usd"], settings["stage_limits"]
                ).snapshot()
            STATE.mkdir(parents=True, exist_ok=True)
            (STATE / "state.json").write_text(json.dumps(snapshot))
            upload("status", json.dumps(snapshot).encode())
            signature = [
                (str(p), p.stat().st_mtime_ns)
                for root in (RUN, CALIBRATION)
                for p in (root / "trials").glob("*/result.json")
            ]
            signature += [
                (str(p), p.stat().st_mtime_ns)
                for p in (ROOT / "reports").glob("*.json")
            ]
            if signature != previous:
                publish()
                backup()
                previous = signature
                last_backup = time.time()
            elif time.time() - last_backup > 60:
                backup()
                last_backup = time.time()
        except Exception as exc:
            # Keep computation running through transient monitor/network errors.
            print("Publication retry:", type(exc).__name__, str(exc)[:200], flush=True)
        stop.wait(10)


def run_phase(name, argv, message):
    with state_lock:
        state.update(phase=name, message=message)
    log = STATE / (name + ".log")
    with log.open("w") as output:
        process = subprocess.Popen(
            argv, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT
        )
        while process.poll() is None:
            time.sleep(2)
            # Setup output only. Paid controllers log to private trial directories.
            lines = log.read_text(errors="replace").splitlines()[-12:]
            lines = [
                line
                for line in lines
                if not any(marker in line for marker in ("sk-or-", "gho_", TOKEN))
            ]
            with state_lock:
                state["log"] = [line[-500:] for line in lines]
        if process.returncode:
            raise RuntimeError(
                f"{name} stopped (exit {process.returncode}); inspect setup evidence"
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-setup", action="store_true")
    args = parser.parse_args()
    STATE.mkdir(parents=True, exist_ok=True)
    handle = (STATE / "worker.lock").open("w")
    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (STATE / "started").exists():
        ledger = ROOT / ".cache/campaign/ledger.sqlite"
        if not args.resume_setup or (STATE / "paid-start-authorized").exists():
            raise RuntimeError(
                "Campaign already started; reconcile it before explicit resumption"
            )
        if ledger.exists():
            settings = config()
            if Ledger(
                ledger, settings["campaign_limit_usd"], settings["stage_limits"]
            ).snapshot()["trials"]:
                raise RuntimeError("Paid attempts exist; setup-only restart is refused")
    (STATE / "started").write_text(str(time.time()))
    draft(RUN)
    draft(CALIBRATION)
    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    cli = [sys.executable, "-m", "codeloom_benchmark.cli"]
    try:
        run_phase("validating", cli + ["validate"], "Validating the ten-task protocol")
        run_phase(
            "certifying",
            cli + ["certify"],
            "Building pinned images and checking references, no-ops, regressions and mutants",
        )
        run_phase(
            "compatibility",
            cli + ["compatibility"],
            "Checking both native adapters without paid inference",
        )
        with state_lock:
            state.update(
                phase="awaiting_paid_summary",
                message="Offline setup passed. Waiting for the owner-facing summary before any paid calls.",
            )
        while not (STATE / "paid-start-authorized").exists():
            time.sleep(5)
        run_phase(
            "calibrating",
            cli + ["calibrate", "--run-dir", str(CALIBRATION)],
            "Four development attempts; fixed $8 development ceiling",
        )
        run_phase(
            "recording_setup",
            ["git", "add", "reports", "suites/v1/dependencies"],
            "Recording certified and calibrated evidence",
        )
        run_phase(
            "committing_setup",
            [
                "git",
                "commit",
                "-m",
                "Record Railway certification and paid calibration before preregistration",
            ],
            "Committing pre-measurement evidence",
        )
        run_phase(
            "preregistering",
            cli + ["preregister"],
            "Freezing the task schedule before measured calls",
        )
        # Publish preregistration before inference, using a host-only Git credential helper.
        run_phase(
            "publishing_protocol",
            ["git", "push", "origin", "HEAD", "benchmark-v1-preregistered"],
            "Publishing the preregistration commit and timestamped tag",
        )
        run_phase(
            "measuring",
            cli + ["run", "--run-dir", str(RUN)],
            "Running the frozen paired schedule; completed attempts publish automatically",
        )
        with state_lock:
            state.update(
                phase="complete",
                message="Campaign finished. Sealed results and evidence are available.",
            )
    except Exception as exc:
        with state_lock:
            state.update(phase="blocked", message=str(exc))
        print(str(exc), flush=True)
    finally:
        # Final publication is required before marking the remote status settled.
        stop.set()
        thread.join(timeout=180)
        try:
            publish()
            backup()
            with state_lock:
                final = dict(
                    state, heartbeat_at=time.time(), finished_at=time.time(), active=[]
                )
            upload("status", json.dumps(final).encode())
        except Exception as exc:
            print("Final publication failed:", exc, flush=True)
            raise


if __name__ == "__main__":
    main()
