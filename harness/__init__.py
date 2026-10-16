"""Local paired benchmark: same tasks, two systems, isolated Docker runs."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYSTEMS = ("mini-swe-agent", "engine-core")


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def read_json(path: Path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def run(args, cwd=None, timeout=600, check=True, env=None):
    result = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env
        or (
            {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            if args and args[0] == "git"
            else None
        ),
    )
    if check and result.returncode:
        raise RuntimeError(
            f"Command failed: {args[:4]}\n{result.stderr[-2500:]}\n{result.stdout[-2500:]}"
        )
    return result
