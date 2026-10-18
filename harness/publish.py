from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from jsonschema import validate

from . import ROOT, write_json
from .report import report
from .traces import export_run_views

# Per-trial proxy-issued bearer tokens (harness/proxy.py: secrets.token_urlsafe(24))
# end up logged verbatim by mini-swe-agent's own trace/view output (its init
# config, including the docker run_args and model_kwargs it was given, gets
# written into native-trace.json and carried into view.json). Those tokens
# are already dead once the per-run proxy process closes, but they must not
# ship into a public repo regardless. Scrub every place one of these is known
# to surface, by context rather than by guessing the token's shape.
_TOKEN_CONTEXTS = [
    re.compile(r"(ENGINE_RESEARCH_TOKEN=)[^\s\"\\]+"),
    re.compile(r'("api_key"\s*:\s*")[^"]+(")'),
    re.compile(r"(Bearer\s+)[A-Za-z0-9_\-]{16,}"),
]


def _scrub_credentials(text: str) -> str:
    for pattern in _TOKEN_CONTEXTS:
        text = pattern.sub(
            lambda m: m.group(1) + "<redacted>" + (m.group(2) if m.lastindex and m.lastindex >= 2 else ""),
            text,
        )
    return text


def publish(run_dir: Path, pages: Path) -> dict:
    summary = report(run_dir)
    schema_name = "site-summary-v2.json" if summary["schema_version"] == 2 else "site-summary.json"
    validate(summary, json.loads((ROOT / "schemas" / schema_name).read_text()))
    target = pages / "packages/ui/src/landings/engine/results/data/benchmark.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(run_dir / "site/summary.json", target)
    assets = pages / "apps/web/public/benchmarks" / summary["run_id"]
    if assets.exists():
        shutil.rmtree(assets)
    assets.mkdir(parents=True)
    for name in ("summary.json", "summary.csv", "report.md", "attempts.jsonl"):
        if (run_dir / name).exists():
            shutil.copy2(run_dir / name, assets / name)
    _copy_public_trials(run_dir, assets)
    views = export_run_views(assets, summary["run_id"], f"/benchmarks/{summary['run_id']}")
    write_json(pages / "packages/ui/src/landings/engine/results/data/run-index.json", views)
    write_json(assets / "runs.json", views)
    shutil.make_archive(str(assets / "evidence"), "zip", assets)
    return summary


def _copy_public_trials(run_dir: Path, assets: Path) -> None:
    for trial in sorted((run_dir / "trials").glob("*")):
        if not (trial / "result.json").exists():
            continue
        dest = assets / "trials" / trial.name
        dest.mkdir(parents=True, exist_ok=True)
        for name in (
            "result.json",
            "attempt.json",
            "verification.json",
            "native-trace.json",
            "engine-events.jsonl",
            "patch.diff",
            "broker.jsonl",
            "controller.log",
            "engine-process.log",
            "view.json",
        ):
            source = trial / name
            if source.exists():
                text = source.read_text()
                if "sk-or-v1-" in text:
                    raise ValueError("Credential marker in export")
                (dest / name).write_text(_scrub_credentials(text))
        for directory in ("browser", "research"):
            source = trial / directory
            if source.exists():
                shutil.copytree(source, dest / directory, dirs_exist_ok=True)
    blobs = run_dir / "blobs"
    if blobs.exists():
        shutil.copytree(blobs, assets / "blobs", dirs_exist_ok=True)
