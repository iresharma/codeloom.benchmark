from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .artifacts import write_json
from .broker import Broker
from .fixtures import command
from .ledger import Ledger
from .manifests import ROOT, config, tasks, fingerprint, model_metadata
from .reporting import INFRA, report
from .runner import attempt
from .sandbox import Network


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
    )
    if ledger.snapshot()["unknown_calls"] or ledger.snapshot()["overrun"]:
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
            unfinished = sum(not p.exists() for p in existing)
            if (
                broker.ledger.headroom("measured")
                < unfinished * settings["trial_limit_usd"]
            ):
                raise ValueError("Not enough budget to admit the remaining pair")
            for row, path in zip(pair, existing):
                trial_id = path.parent.name
                if path.exists():
                    result = json.loads(path.read_text())
                else:
                    if any(
                        t["id"] == trial_id for t in broker.ledger.snapshot()["trials"]
                    ):
                        raise ValueError(
                            "Interrupted attempt already has a ledger entry; preserve and reconcile it before recovery"
                        )
                    result = attempt(
                        by_id[row["task_id"]],
                        row["system"],
                        images[row["task_id"]],
                        broker,
                        network,
                        root,
                        trial_id,
                        "measured",
                        lock["calibration"]["watchdog_s"],
                        settings["trial_limit_usd"],
                    )
                print(
                    trial_id,
                    result["termination"],
                    f"${result['cost_usd']:.4f}",
                    flush=True,
                )
                if (
                    result["termination"] in INFRA
                    and broker.ledger.headroom("recovery") >= 1
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
                        1,
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
            for system in config()["systems"]:
                result = attempt(
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
                results.append(result)
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
