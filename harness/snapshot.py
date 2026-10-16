"""Pinned upstream trees plus explicit setup recipes."""

from __future__ import annotations

import fnmatch
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

from . import ROOT, run

IGNORED = {
    ".git",
    ".engine",
    "node_modules",
    "distribution",
    "dist",
    "__pycache__",
    ".pytest_cache",
    ".coverage",
}


def apply_recipe(root: Path, recipe: dict) -> None:
    for rel, changes in (recipe or {}).items():
        path = root / rel
        if path.is_symlink():
            raise ValueError(f"Refuse recipe symlink: {rel}")
        if isinstance(changes, str):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(changes)
            continue
        text = path.read_text()
        for change in changes:
            old, new = change["old"], change["new"]
            expected = change.get("count", 1)
            if text.count(old) != expected:
                raise ValueError(f"Recipe drift: {rel}, expected {expected} matches")
            text = text.replace(old, new)
        path.write_text(text)


def repo_cache(task: dict) -> Path:
    path = ROOT / ".cache/repos" / task["repo"]
    if not path.exists():
        run(["git", "clone", "--no-checkout", task["upstream"], str(path)])
    probe = run(
        ["git", "cat-file", "-e", task["commit"] + "^{commit}"],
        cwd=path,
        check=False,
    )
    if probe.returncode:
        run(["git", "fetch", "--depth", "1", "origin", task["commit"]], cwd=path)
    return path


def _materialize_links(root: Path) -> None:
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        target = path.resolve(strict=True)
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError("Unsupported upstream link: " + str(path.relative_to(root)))
        content = target.read_bytes()
        path.unlink()
        path.write_bytes(content)


def snapshot(task: dict, target: Path, reference=False) -> Path:
    source = repo_cache(task)
    target.mkdir(parents=True, exist_ok=False)
    raw = subprocess.check_output(
        ["git", "archive", "--format=tar", task["commit"]], cwd=source
    )
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r|") as archive:
        archive.extractall(target, filter="data")
    _materialize_links(target)
    lock = ROOT / "suites" / task["suite"] / "dependencies" / task["repo"] / "package-lock.json"
    if lock.exists() and task["suite"] != "v2":
        shutil.copy2(lock, target / "package-lock.json")
    apply_recipe(target, task.get("setup") or {})
    if lock.exists() and task["suite"] == "v2":
        node_root = (task.get("runtime") or {}).get("node_workdir") or ""
        shutil.copy2(lock, target / node_root / "package-lock.json")
    if reference:
        apply_recipe(target, task.get("reference") or {})
    (target / ".bench").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "public/validate_answer.py", target / ".bench/validate_answer.py")
    shutil.copy2(
        ROOT / "schemas/navigation-answer.json",
        target / ".bench/navigation-answer.schema.json",
    )
    run(["git", "init", "-q", "-b", "main"], cwd=target)
    run(["git", "config", "user.name", "Benchmark"], cwd=target)
    run(["git", "config", "user.email", "benchmark@localhost"], cwd=target)
    run(["git", "config", "core.hooksPath", "/dev/null"], cwd=target)
    run(["git", "config", "commit.gpgSign", "false"], cwd=target)
    run(["git", "add", "."], cwd=target)
    run(["git", "commit", "-q", "-m", "Task starting snapshot"], cwd=target)
    return target


def file_map(root: Path) -> dict[str, bytes]:
    rows = {}
    for path in root.rglob("*"):
        rel = path.relative_to(root)
        if any(part in IGNORED for part in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"Output symlink forbidden: {rel}")
        if path.is_file():
            rows[rel.as_posix()] = path.read_bytes()
    return rows


def output_changes(baseline: Path, candidate: Path, task: dict):
    before, after = file_map(baseline), file_map(candidate)
    changes = {
        path: after.get(path)
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    }
    disallowed = [
        path
        for path in changes
        if not any(fnmatch.fnmatchcase(path, glob) for glob in task["allowed_paths"])
    ]
    return changes, disallowed


def stage_output(baseline: Path, changes: dict, destination: Path) -> Path:
    shutil.copytree(
        baseline,
        destination,
        ignore=shutil.ignore_patterns(".git", ".engine", "node_modules"),
    )
    for rel, content in changes.items():
        path = destination / rel
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    return destination


def save_patch(baseline: Path, candidate: Path, destination: Path) -> None:
    import tempfile

    changes, _ = output_changes(baseline, candidate, {"allowed_paths": ["*"]})
    with tempfile.TemporaryDirectory() as tmp:
        trusted = Path(tmp) / "patch"
        shutil.copytree(baseline, trusted)
        for rel, content in changes.items():
            path = trusted / rel
            if content is None:
                path.unlink(missing_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        result = run(
            ["git", "diff", "--no-index", "--", str(baseline), str(trusted)],
            check=False,
        )
        text = result.stdout
        destination.write_text(text if text else "")
