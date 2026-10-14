"""One unscored light fixture, both systems; $0.50 per arm from development."""

import fcntl
import argparse
import json
import re
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import worker as monitoring  # noqa: E402
from codeloom_benchmark.cli import draft  # noqa: E402
from codeloom_benchmark.compatibility import check  # noqa: E402
from codeloom_benchmark.manifests import ROOT, tasks, config  # noqa: E402
from codeloom_benchmark.sandbox import task_image, audit_image  # noqa: E402
from codeloom_benchmark.evaluate import certify  # noqa: E402
from codeloom_benchmark.campaign import services, parallel_pair  # noqa: E402
from codeloom_benchmark.runner import attempt  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", default="railway-smoke")
    args = parser.parse_args()
    if not re.fullmatch(r"railway-smoke(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?", args.run_id):
        raise ValueError("Use a railway-smoke run ID with lowercase letters, numbers and hyphens")
    monitoring.STATE.mkdir(parents=True, exist_ok=True)
    lock = (monitoring.STATE / "worker.lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    root = ROOT / "runs" / args.run_id
    if (root / "trials").exists():
        raise RuntimeError("Smoke already attempted; preserve its costs and evidence")
    draft(root)
    thread = threading.Thread(target=monitoring.heartbeat, daemon=True)
    thread.start()
    results = []
    try:
        monitoring.state.update(
            phase="smoke_preparing",
            message="Unscored light Python fixture; $0.50 actual target per arm, $1.50 call gates. Official study remains held.",
        )
        task = tasks("smoke")[0]
        image = task_image(task)
        audit_image(image, task)
        certificate = certify(task, image)
        if certificate["status"] != "certified":
            raise RuntimeError("Smoke fixture did not certify")
        monitoring.state.update(
            phase="smoke_compatibility",
            message="Verifying native reasoning and effort before the paid smoke test",
        )
        gate = check(ROOT.parent / "engine", ROOT / "reports/compatibility.json")
        if gate["status"] != "passed":
            raise RuntimeError("Native compatibility gate failed")
        broker, network = services(root)
        monitoring.state.update(
            phase="smoke_running",
            message="Two unscored light attempts running concurrently; $1 combined actual-spend target, final calls may overshoot",
        )
        try:

            def execute(system):
                return attempt(
                    task,
                    system,
                    image,
                    broker,
                    network,
                    root,
                    f"{args.run_id}-{task['id']}-{system}",
                    "development",
                    600,
                    0.5,
                )

            results = parallel_pair(config()["systems"], execute)
        finally:
            network.close()
            broker.close()
        passed = sum(
            r["termination"] == "completed"
            and r["verification"]["pass"]
            and not r["cost_is_conservative"]
            for r in results
        )
        cost = sum(r["cost_usd"] for r in results)
        monitoring.state.update(
            phase="complete",
            message=f"Unscored smoke test: {passed}/2 arms passed, ${cost:.4f} spent/reserved. Official calibration and measurement have not started.",
        )
        print(json.dumps(results, indent=2), flush=True)
    except Exception as exc:
        monitoring.state.update(phase="blocked", message="Smoke stopped: " + str(exc))
        raise
    finally:
        monitoring.stop.set()
        thread.join(timeout=180)
        monitoring.publish()
        monitoring.backup()
        monitoring.upload(
            "status",
            json.dumps(monitoring.final_status()).encode(),
        )


if __name__ == "__main__":
    main()
