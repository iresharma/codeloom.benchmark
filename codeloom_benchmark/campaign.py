from __future__ import annotations

import json
import os
import re
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

from .artifacts import write_json
from .broker import Broker
from .fixtures import command
from .ledger import Ledger
from .manifests import ROOT, config, tasks, fingerprint, model_metadata, task_limit
from .reporting import INFRA, report
from .runner import attempt
from .sandbox import Network


def parallel_pair(jobs, execute):
    """At most one matched pair at a time; wait for both before budget/recovery decisions."""
    workers = config().get("parallel_attempts", 1)
    if workers not in (1, 2):
        raise ValueError("parallel_attempts must be 1 or 2")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(execute, jobs))


def credentials():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        env_path = ROOT.parents[1] / "env.sh"
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                match = re.match(
                    r"^\s*(?:export\s+)?OPENROUTER_API_KEY\s*=\s*[\"\']?([^\"\'\s]+)",
                    line,
                )
                if match:
                    key = match[1]
    if not key or key in ("...", "your-key", "changeme"):
        raise ValueError("Set OPENROUTER_API_KEY in the host environment")
    return key


def services(root, calibration=None):
    settings = config()
    if not settings.get("paid_runs_authorized", False):
        raise ValueError("Paid runs are on hold; explicit owner approval is required")
    if calibration is not None:
        settings["request_timeout_s"] = calibration["request_timeout_s"]
    from .pricing import check as check_pricing

    if check_pricing()["status"] != "passed":
        raise ValueError("Pinned provider price exceeds the fixed admission rates")
    # The same durable campaign ledger is shared by calibration, measurement and retries.
    ledger = Ledger(
        ROOT / ".cache/campaign/ledger.sqlite",
        settings["campaign_limit_usd"],
        settings["stage_limits"],
        budget_policy=settings.get("budget_policy", "strict-reservation"),
        call_limit_multiplier=settings.get("call_limit_multiplier", 3),
    )
    if (
        ledger.snapshot()["unknown_calls"]
        or ledger.snapshot()["overrun"]
        or ledger.snapshot()["protocol_violation"]
    ):
        raise ValueError(
            "Resolve unknown bills or provider price overruns before admitting another paid call"
        )
    if ledger.active_trials():
        raise ValueError(
            "An active or interrupted trial must be reconciled and revoked before starting another broker"
        )
    if calibration is not None:
        ledger.claim_run("measured", root)
    broker = Broker(
        ledger, root, credentials(), settings, model_metadata=model_metadata()
    )
    port = broker.start("0.0.0.0")
    try:
        network = Network(port)
    except BaseException:
        broker.close()
        raise
    return broker, network


def measured(root: Path, lock):
    settings = config()
    manifest = json.loads((root / "manifest.json").read_text())
    manifest.update(
        calibration_status="passed",
        preregistration_commit=command(
            ["git", "rev-parse", "benchmark-v1-preregistered^{commit}"], cwd=ROOT
        ),
    )
    write_json(root / "manifest.json", manifest)
    broker, network = services(root, lock["calibration"])
    by_id = {t["id"]: t for t in tasks()}
    images = {
        r["id"]: r["image"]
        for r in json.loads((ROOT / "reports/certification.json").read_text())["tasks"]
    }
    try:
        for i in range(0, len(lock["schedule"]), 2):
            pair = lock["schedule"][i : i + 2]
            existing = [
                root
                / "trials"
                / f"{root.name}-{r['task_id']}-{r['system']}"
                / "result.json"
                for r in pair
            ]
            remaining_caps = sum(
                task_limit(by_id[r["task_id"]], settings)
                for r, p in zip(pair, existing)
                if not p.exists()
            )
            if broker.ledger.headroom("measured") < remaining_caps:
                raise ValueError("Not enough budget to admit the remaining pair")
            ledger_ids = {t["id"] for t in broker.ledger.snapshot()["trials"]}
            if any(
                not path.exists() and path.parent.name in ledger_ids
                for path in existing
            ):
                raise ValueError(
                    "Interrupted attempt already has a ledger entry; preserve and reconcile it before recovery"
                )

            def execute(job):
                row, path = job
                trial_id = path.parent.name
                if path.exists():
                    return json.loads(path.read_text())
                return attempt(
                    by_id[row["task_id"]],
                    row["system"],
                    images[row["task_id"]],
                    broker,
                    network,
                    root,
                    trial_id,
                    "measured",
                    lock["calibration"]["watchdog_s"],
                    task_limit(by_id[row["task_id"]], settings),
                )

            results = parallel_pair(list(zip(pair, existing)), execute)
            report(root)
            snapshot = broker.ledger.snapshot()
            if (
                any(r["termination"] == "protocol_violation" for r in results)
                or snapshot["unknown_calls"]
                or snapshot["overrun"]
            ):
                raise ValueError("Provider identity/usage gate stopped the campaign")
            for row, path, result in zip(pair, existing, results):
                trial_id = path.parent.name
                print(
                    trial_id,
                    result["termination"],
                    f"${result['cost_usd']:.4f}",
                    flush=True,
                )
                if (
                    result["termination"] in INFRA
                    and broker.ledger.headroom("recovery")
                    >= task_limit(by_id[row["task_id"]], settings)
                    and not broker.ledger.snapshot()["unknown_calls"]
                ):
                    retry = trial_id + "-recovery"
                    if (root / "trials" / retry / "result.json").exists():
                        continue
                    if any(
                        t["id"] == retry for t in broker.ledger.snapshot()["trials"]
                    ):
                        raise ValueError("Interrupted recovery cannot be repeated")
                    attempt(
                        by_id[row["task_id"]],
                        row["system"],
                        images[row["task_id"]],
                        broker,
                        network,
                        root,
                        retry,
                        "recovery",
                        lock["calibration"]["watchdog_s"],
                        task_limit(by_id[row["task_id"]], settings),
                        retry_of=trial_id,
                    )
                report(root)
                if (
                    result["termination"] == "protocol_violation"
                    or broker.ledger.snapshot()["unknown_calls"]
                    or broker.ledger.snapshot()["overrun"]
                ):
                    raise ValueError(
                        "Provider identity/usage gate stopped the campaign"
                    )
    finally:
        network.close()
        broker.close()
        report(root)
    return report(root)


def calibrate(root, engine):
    if not config().get("paid_runs_authorized", False):
        raise ValueError("Paid runs are on hold; explicit owner approval is required")
    from .compatibility import check
    from .sandbox import task_image, audit_image

    compatibility = check(engine, ROOT / "reports/compatibility.json")
    if compatibility["status"] != "passed":
        raise ValueError(compatibility["reason"])
    dev = tasks("dev")
    if len(dev) != 2:
        raise ValueError("Exactly two representative development tasks required")
    from .evaluate import certify

    images = {}
    for task in dev:
        image = task_image(task)
        audit_image(image, task)
        if certify(task, image)["status"] != "certified":
            raise ValueError(
                "Development fixture must certify before paid calibration: "
                + task["id"]
            )
        images[task["id"]] = image
    root.mkdir(parents=True, exist_ok=True)
    broker, network = services(root)
    results = []
    try:
        for task in dev:
            image = images[task["id"]]

            def execute(system):
                return attempt(
                    task,
                    system,
                    image,
                    broker,
                    network,
                    root,
                    f"{root.name}-{task['id']}-{system}",
                    "development",
                    7200,
                    1.5,
                )

            results.extend(parallel_pair(config()["systems"], execute))
            snapshot = broker.ledger.snapshot()
            if (
                snapshot["unknown_calls"]
                or snapshot["overrun"]
                or snapshot.get("protocol_violation")
            ):
                raise ValueError("Provider identity/usage gate stopped calibration")
        passed = all(
            r["termination"] == "completed"
            and r["verification"]["pass"]
            and not r["cost_is_conservative"]
            for r in results
        )
        from .runner import journal

        calls = [
            call
            for result in results
            for call in journal(root / "trials" / result["id"])
            if call.get("status") == "complete"
        ]
        throughput = [
            c["generated_tokens_per_elapsed_s"]
            for c in calls
            if c.get("generated_tokens_per_elapsed_s", 0) > 0
        ]
        latency = [
            c["first_byte_s"] for c in calls if c.get("first_byte_s") is not None
        ]
        passed = passed and bool(throughput) and bool(latency)
        watchdog = max(
            3600,
            2
            * max(
                (r["elapsed_s"] for r in results if r["termination"] == "completed"),
                default=0,
            )
            + 120,
        )
        # Use the slowest observed rate, including first-byte time, plus a twofold margin.
        timeout = (
            min(
                watchdog,
                max(
                    300,
                    2 * (config()["max_tokens"] / min(throughput) + max(latency)) + 120,
                ),
            )
            if throughput and latency
            else config()["request_timeout_s"]
        )
        record = {
            "status": "passed" if passed else "failed",
            "attempts": results,
            "suite_fingerprint": fingerprint()[0],
            "runtime_image": json.loads((ROOT / ".cache/runtime.json").read_text())[
                "image"
            ],
            "parallel_attempts": config().get("parallel_attempts", 1),
            "budget_policy": config().get("budget_policy", "strict-reservation"),
            "call_limit_multiplier": config().get("call_limit_multiplier", 3),
            "watchdog_s": watchdog,
            "request_timeout_s": timeout,
            "observed_generated_tokens_per_elapsed_s": throughput,
            "observed_first_byte_s": latency,
            "policy": "Failed completion, cap, unknown usage or verification stops measurement.",
        }
        write_json(ROOT / "reports/calibration.json", record)
        return record
    finally:
        network.close()
        broker.close()
