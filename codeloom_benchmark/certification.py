"""Reuse CPU certification only when the complete per-task grading contract agrees."""

import hashlib
import json
from pathlib import Path

from .artifacts import canonical, digest
from .manifests import ROOT

GRADE_FILES = (
    "codeloom_benchmark/certification.py",
    "codeloom_benchmark/evaluate.py",
    "codeloom_benchmark/manifests.py",
    "codeloom_benchmark/fixtures.py",
    "codeloom_benchmark/sandbox.py",
    "codeloom_benchmark/artifacts.py",
    "public/validate_answer.py",
    "schemas/navigation-answer.json",
    "pyproject.toml",
    "uv.lock",
    "configs/core.yaml",
)


def grading_fingerprint(task: dict, root: Path = ROOT):
    runtime = json.loads((root / ".cache/runtime.json").read_text())
    paths = [root / rel for rel in GRADE_FILES]
    paths += sorted((root / "codeloom_benchmark/graders").glob("*.py"))
    suite = "dev" if task["id"].startswith("dev-") else "v1"
    paths += sorted((root / "evaluator" / suite / task["id"]).rglob("*"))
    lock = root / "suites/v1/dependencies" / task["repo"] / "package-lock.json"
    if lock.exists():
        paths.append(lock)
    files = {str(p.relative_to(root)): digest(p) for p in paths if p.is_file()}
    if any(not (root / rel).is_file() for rel in GRADE_FILES):
        raise ValueError("Incomplete grading implementation")
    return hashlib.sha256(
        canonical({"files": files, "task": task, "runtime_image": runtime["image"]})
    ).hexdigest()
