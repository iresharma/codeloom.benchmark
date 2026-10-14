from __future__ import annotations

import argparse
import hashlib
import json
import platform
import random
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from .artifacts import write_json, canonical
from .compatibility import check
from .evaluate import certify
from .certification import grading_fingerprint
from .fixtures import command
from .manifests import (
    ROOT,
    check_frozen,
    config,
    freeze,
    tasks,
    validate_suite,
    fingerprint,
)
from .reporting import report
from .sandbox import audit_image, prepare_runtime, task_image, dependency_image


def draft(root):
    root.mkdir(parents=True, exist_ok=True)
    manifest = {
        "suite": "v1",
        "run_id": root.name,
        "settings": config(),
        "tasks": tasks(),
        "platform": "linux/amd64",
        "host_architecture": platform.machine(),
        "calibration_status": "not_run",
        "limitations": [
            "Author-designed corpus; no independent review.",
            "One run per task; no reliability, interaction, or component-effect claims.",
            "Synthetic bugs keep upstream tests, which can reveal the regression.",
            "Test-generation features are supplied with their reference tests withheld.",
            "Host ARM64 runs Linux AMD64 through emulation.",
            "Offline report rebuilding is reproducible; model outputs, endpoint availability and prices are not.",
        ],
    }
    write_json(root / "manifest.json", manifest)
    return report(root)


def certification(engine, rebuild=True):
    validate_suite()
    runtime = (
        prepare_runtime(engine)
        if rebuild
        else json.loads((ROOT / ".cache/runtime.json").read_text())
    )
    # Resolve and lock dependencies before calculating one immutable suite fingerprint.
    for task in tasks():
        if task["language"] == "typescript":
            dependency_image(task)
            break
    initial_fingerprint = fingerprint()[0]
    records = []
    result = {
        "status": "in_progress",
        "runtime": runtime,
        "tasks": records,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "suite_fingerprint": initial_fingerprint,
    }
    write_json(ROOT / "reports/certification.json", result)
    for task in tasks():
        print("Certifying", task["id"], flush=True)
        try:
            cache = ROOT / ".cache/certification" / task["id"] / "certification.json"
            cached = json.loads(cache.read_text()) if cache.exists() else {}
            contract = grading_fingerprint(task)
            reusable = (
                cached.get("status") == "certified"
                and cached.get("grading_fingerprint") == contract
            )
            if reusable:
                try:
                    image = cached["image"]
                    labels = json.loads(
                        command(
                            [
                                "docker",
                                "image",
                                "inspect",
                                image,
                                "--format",
                                "{{json .Config.Labels}} ",
                            ]
                        )
                    )
                    reusable = (
                        labels.get("org.codeloom.runtime-image") == runtime["image"]
                        and labels.get("org.codeloom.task-fingerprint")
                        == hashlib.sha256(canonical(task)).hexdigest()
                    )
                except RuntimeError:
                    reusable = False
            if reusable:
                record = cached
                record.setdefault(
                    "original_suite_fingerprint", record["suite_fingerprint"]
                )
                record["suite_fingerprint"] = initial_fingerprint
                record["cache_reused"] = True
                print(
                    "Reusing unchanged certified grading inputs", task["id"], flush=True
                )
            else:
                image = task_image(task)
                record = certify(task, image)
            record["audit"] = audit_image(image, task)
            if grading_fingerprint(task) != contract:
                raise ValueError("Grading inputs changed during certification")
            record["grading_fingerprint"] = contract
            write_json(cache, record)
        except Exception as exc:
            record = {
                "id": task["id"],
                "status": "failed",
                "error": str(exc)[-4000:],
                "suite_fingerprint": initial_fingerprint,
            }
        if fingerprint()[0] != initial_fingerprint:
            raise ValueError(
                "Suite changed during certification; restart on stable files"
            )
        records.append(record)
        write_json(ROOT / "reports/certification.json", result)
        print(task["id"], record["status"], flush=True)
    result["status"] = (
        "certified" if all(r["status"] == "certified" for r in records) else "failed"
    )
    write_json(ROOT / "reports/certification.json", result)
    return result


def schedule():
    rng = random.Random(config()["schedule_seed"])
    ordered = tasks()
    rng.shuffle(ordered)
    result = []
    for i, task in enumerate(ordered):
        arms = list(config()["systems"])
        if i % 2:
            arms.reverse()
        result.extend({"task_id": task["id"], "system": arm} for arm in arms)
    return result


def preregister():
    certified = json.loads((ROOT / "reports/certification.json").read_text())
    calibration = json.loads((ROOT / "reports/calibration.json").read_text())
    if (
        certified["status"] != "certified"
        or calibration["status"] != "passed"
        or certified.get("suite_fingerprint") != fingerprint()[0]
        or calibration.get("suite_fingerprint") != fingerprint()[0]
        or calibration.get("runtime_image") != certified["runtime"]["image"]
    ):
        raise ValueError(
            "All tasks must certify and representative calibration must pass before preregistration"
        )
    if command(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError(
            "Commit implementation and certification before preregistering"
        )
    runtime = certified["runtime"]
    if runtime["engine_dirty"]:
        raise ValueError("Certify a committed engine revision")
    for task in tasks():
        row = next(r for r in certified["tasks"] if r["id"] == task["id"])
        labels = json.loads(
            command(
                [
                    "docker",
                    "image",
                    "inspect",
                    row["image"],
                    "--format",
                    "{{json .Config.Labels}} ",
                ]
            )
        )
        if (
            row.get("suite_fingerprint") != fingerprint()[0]
            or labels.get("org.codeloom.runtime-image") != runtime["image"]
        ):
            raise ValueError(
                "Mixed or stale certification runtime; recertify the suite"
            )
    lock = freeze("v1", runtime, schedule(), calibration)
    command(["git", "add", "suites/v1/suite.lock.json"], cwd=ROOT)
    command(
        [
            "git",
            "commit",
            "-m",
            "Preregister v1 paired benchmark before measured calls",
        ],
        cwd=ROOT,
    )
    tag = "benchmark-v1-preregistered"
    command(
        ["git", "tag", "-a", tag, "-m", datetime.now(timezone.utc).isoformat()],
        cwd=ROOT,
    )
    return lock


def run(root, engine):
    # Offline setup goes first. No credentials are read until all fixed gates pass.
    if not root.exists():
        draft(root)
    compatibility = check(engine, ROOT / "reports/compatibility.json")
    if compatibility["status"] != "passed":
        manifest = json.loads((root / "manifest.json").read_text())
        manifest["blocked_reason"] = compatibility["reason"]
        write_json(root / "manifest.json", manifest)
        report(root)
        raise ValueError(compatibility["reason"])
    lock = check_frozen()
    calibration = lock["calibration"]
    if calibration["status"] != "passed":
        raise ValueError("Calibration gate failed")
    if command(["git", "status", "--porcelain"], cwd=ROOT):
        raise ValueError("Measured checkout must be clean")
    from .campaign import measured

    return measured(root, lock)


def import_site(root, pages):
    summary = report(root)
    from jsonschema import validate

    validate(summary, json.loads((ROOT / "schemas/site-summary.json").read_text()))
    target = pages / "packages/ui/src/landings/engine/results/data/benchmark.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "site/summary.json", target)
    assets = pages / "apps/web/public/benchmarks" / summary["run_id"]
    assets.mkdir(parents=True, exist_ok=True)
    for name in (
        "summary.json",
        "summary.csv",
        "report.md",
        "attempts.jsonl",
        "examples.json",
    ):
        shutil.copy2(root / name, assets / name)
    from .export import export_traces

    setup = assets / "setup"
    setup.mkdir(exist_ok=True)
    for name in (
        "compatibility.json",
        "pricing.json",
        "certification.json",
        "excluded-ky.json",
        "authoring-failures.json",
        "cost-analysis.json",
        "cost-analysis.md",
        "provider-snapshot.json",
    ):
        if (ROOT / "reports" / name).exists():
            shutil.copy2(ROOT / "reports" / name, setup / name)
    if (ROOT / "reports/history").exists():
        shutil.copytree(ROOT / "reports/history", setup / "history", dirs_exist_ok=True)
    if (ROOT / "reports/adapter-check").exists():
        shutil.copytree(
            ROOT / "reports/adapter-check", setup / "adapter-check", dirs_exist_ok=True
        )
    (assets / "evidence.zip").unlink(missing_ok=True)
    export_traces(root, assets)
    from .trace_view import export_run_views
    from .artifacts import checksum_tree

    run_views = export_run_views(
        assets, summary["run_id"], f"/benchmarks/{summary['run_id']}"
    )
    adapter = setup / "adapter-check"
    if adapter.exists():
        run_views += export_run_views(
            adapter,
            "adapter-check",
            f"/benchmarks/{summary['run_id']}/setup/adapter-check",
            synthetic=True,
        )
    write_json(
        pages / "packages/ui/src/landings/engine/results/data/run-index.json", run_views
    )
    write_json(assets / "runs.json", run_views)
    certification = (
        json.loads((setup / "certification.json").read_text())
        if (setup / "certification.json").exists()
        else {}
    )
    compatibility = (
        json.loads((setup / "compatibility.json").read_text())
        if (setup / "compatibility.json").exists()
        else {}
    )
    pricing = (
        json.loads((setup / "pricing.json").read_text())
        if (setup / "pricing.json").exists()
        else {}
    )
    manifest_limits = json.loads((root / "manifest.json").read_text())["settings"].get(
        "stage_limits", {}
    )
    setup_summary = {
        "certification_status": certification.get("status", "not_run"),
        "certified_tasks": sum(
            t.get("status") == "certified" for t in certification.get("tasks", [])
        ),
        "tasks": [
            {
                "id": t["id"],
                "status": t["status"],
                "audit": t.get("audit", {}).get("status", "unknown"),
                "mutation_score": t.get("reference", {}).get("mutation_score"),
            }
            for t in certification.get("tasks", [])
        ],
        "compatibility_status": compatibility.get("status", "not_run"),
        "compatibility_checks": compatibility.get("checks", {}),
        "pricing_status": pricing.get("status", "not_run"),
        "published_attempts": sum(1 for r in run_views if not r["synthetic"]),
        "budget": [
            {
                "label": name,
                "value": summary["budget_usd"] - sum(manifest_limits.values())
                if name == "Fees / buffer"
                else manifest_limits[stage],
            }
            for name, stage in (
                ("Calibration", "development"),
                ("Measurement", "measured"),
                ("Recovery", "recovery"),
                ("Fees / buffer", "fees"),
            )
        ]
        if manifest_limits
        else [],
    }
    write_json(
        pages / "packages/ui/src/landings/engine/results/data/setup.json", setup_summary
    )
    write_json(assets / "setup-summary.json", setup_summary)
    if adapter.exists():
        checksum_tree(adapter)
    checksum_tree(assets)
    import zipfile

    archive = assets / "evidence.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for source in sorted(assets.rglob("*")):
            if source.is_file() and source != archive:
                info = zipfile.ZipInfo(
                    source.relative_to(assets).as_posix(),
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, source.read_bytes())
    checksum_tree(assets)
    return summary


def main():
    parser = argparse.ArgumentParser(
        description="Budgeted CodeLoom benchmark: fixed gates before scoring"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("validate", "certify", "compatibility", "preregister"):
        p = sub.add_parser(name)
        p.add_argument("--engine", type=Path, default=ROOT.parent / "engine")
    for name in ("draft", "report", "run", "import-site", "calibrate", "cost-analysis"):
        p = sub.add_parser(name)
        p.add_argument("--run-dir", type=Path, default=ROOT / "reports/v1-not-run")
        p.add_argument("--engine", type=Path, default=ROOT.parent / "engine")
        p.add_argument("--pages", type=Path, default=ROOT.parent / "pages")
    args = parser.parse_args()
    try:
        if args.command == "validate":
            result = {"tasks": len(validate_suite()), "status": "valid"}
        elif args.command == "certify":
            result = certification(args.engine)
        elif args.command == "compatibility":
            result = check(args.engine, ROOT / "reports/compatibility.json")
        elif args.command == "preregister":
            result = preregister()
        elif args.command == "calibrate":
            from .campaign import calibrate

            result = calibrate(args.run_dir, args.engine)
        elif args.command == "cost-analysis":
            from .cost_analysis import analyze

            result = analyze()
        elif args.command == "draft":
            result = draft(args.run_dir)
        elif args.command == "report":
            result = report(args.run_dir)
        elif args.command == "import-site":
            result = import_site(args.run_dir, args.pages)
        else:
            result = run(args.run_dir, args.engine)
        print(
            json.dumps(
                {
                    "status": result.get("status"),
                    "task_count": result.get(
                        "task_count",
                        result.get("tasks")
                        if isinstance(result.get("tasks"), int)
                        else None,
                    ),
                },
                sort_keys=True,
            )
        )
        if result.get("status") in ("failed", "review_required"):
            sys.exit(2)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        print("Benchmark stopped: " + str(exc), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
