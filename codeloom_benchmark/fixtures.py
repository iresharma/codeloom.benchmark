"""Build isolated source snapshots from immutable upstream commits and explicit recipes."""

from __future__ import annotations

import fnmatch
import io
import shutil
import subprocess
import tarfile
from pathlib import Path

from .artifacts import write_json
from .manifests import ROOT


def command(args, cwd=None, timeout=600):
    result = subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=timeout
    )
    if result.returncode:
        raise RuntimeError(
            f"Command failed: {args[:3]}\n{result.stderr[-2500:]}\n{result.stdout[-2500:]}"
        )
    return result.stdout.strip()


def repo_source(task):
    path = ROOT / ".cache/repos" / task["repo"]
    if not path.exists():
        command(["git", "clone", "--no-checkout", task["upstream"], str(path)])
    try:
        command(["git", "cat-file", "-e", task["commit"] + "^{commit}"], cwd=path)
    except RuntimeError:
        command(["git", "fetch", "--depth", "1", "origin", task["commit"]], cwd=path)
    return path


def apply_recipe(root: Path, recipe: dict):
    for rel, changes in recipe.items():
        path = root / rel
        if path.is_symlink():
            raise ValueError("Refuse recipe symlink")
        if isinstance(changes, str):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(changes)
        else:
            # A brand-new file starts as "", so a recipe can create one with
            # an {old: "", new: <content>} change instead of the whole-file
            # string form above.
            text = path.read_text() if path.exists() else ""
            for change in changes:
                old, new = change["old"], change["new"]
                expected = change.get("count", 1)
                if text.count(old) != expected:
                    raise ValueError(
                        f"Recipe drift: {rel}, expected {expected} matches"
                    )
                text = text.replace(old, new)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)


def normalize_source_links(root: Path):
    """Materialize trusted upstream file links before sealing the initial snapshot."""
    for path in root.rglob("*"):
        if not path.is_symlink():
            continue
        target = path.resolve(strict=True)
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError(
                "Unsupported upstream link: " + str(path.relative_to(root))
            )
        content = target.read_bytes()
        path.unlink()
        path.write_bytes(content)


def snapshot(task: dict, target: Path, reference=False):
    source = repo_source(task)
    target.mkdir(parents=True, exist_ok=False)
    raw = subprocess.check_output(
        ["git", "archive", "--format=tar", task["commit"]], cwd=source
    )
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(target, filter="data")
    normalize_source_links(target)
    dep_lock = ROOT / "suites/v1/dependencies" / task["repo"] / "package-lock.json"
    if dep_lock.exists():
        shutil.copy2(dep_lock, target / "package-lock.json")
    apply_recipe(target, task["setup"])
    if reference:
        apply_recipe(target, task["reference"])
    (target / ".bench").mkdir()
    shutil.copy2(
        ROOT / "public/validate_answer.py", target / ".bench/validate_answer.py"
    )
    shutil.copy2(
        ROOT / "schemas/navigation-answer.json",
        target / ".bench/navigation-answer.schema.json",
    )
    command(["git", "init", "-q", "-b", "main"], cwd=target)
    command(["git", "config", "user.name", "Benchmark"], cwd=target)
    command(["git", "config", "user.email", "benchmark@localhost"], cwd=target)
    command(["git", "config", "core.hooksPath", "/dev/null"], cwd=target)
    command(["git", "config", "commit.gpgSign", "false"], cwd=target)
    command(["git", "add", "."], cwd=target)
    command(["git", "commit", "-q", "-m", "Task starting snapshot"], cwd=target)
    return target


def capture(container: str, destination: Path):
    """Read the sealed filesystem without trusting agent Git or following its symlinks."""
    destination.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        ["docker", "cp", container + ":/task/.", "-"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    ignored = {
        ".git",
        ".engine",
        "node_modules",
        "distribution",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".coverage",
    }
    total = 0
    try:
        with tarfile.open(fileobj=proc.stdout, mode="r|*") as archive:
            for item in archive:
                rel = Path(item.name)
                if rel.parts and rel.parts[0] in ("task", "."):
                    rel = Path(*rel.parts[1:])
                if rel.is_absolute() or ".." in rel.parts:
                    raise ValueError("Unsafe archive path")
                if any(part in ignored for part in rel.parts):
                    continue
                if item.issym() or item.islnk():
                    raise ValueError("Output symlink forbidden")
                if item.isfile():
                    total += item.size
                    if total > 100_000_000:
                        raise ValueError("Output exceeds 100 MB")
                    target = destination / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(archive.extractfile(item).read())
        if proc.wait() != 0:
            raise RuntimeError("Docker checkout export failed")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def output_changes(baseline: Path, candidate: Path, task: dict):
    ignored = (
        ".git",
        ".engine",
        "node_modules",
        "distribution",
        "dist",
        "__pycache__",
        ".pytest_cache",
        ".coverage",
    )

    def files(root):
        rows = {}
        for p in root.rglob("*"):
            rel = p.relative_to(root)
            if any(n in ignored for n in rel.parts):
                continue
            if p.is_symlink():
                raise ValueError(f"Output symlink forbidden: {rel}")
            if p.is_file():
                rows[rel.as_posix()] = p.read_bytes()
        return rows

    before, after = files(baseline), files(candidate)
    changes = {
        p: after.get(p)
        for p in before.keys() | after.keys()
        if before.get(p) != after.get(p)
    }
    disallowed = [
        p
        for p in changes
        if not any(fnmatch.fnmatchcase(p, g) for g in task["allowed_paths"])
    ]
    return changes, disallowed


def stage_output(baseline: Path, changes: dict, destination: Path):
    shutil.copytree(
        baseline,
        destination,
        ignore=shutil.ignore_patterns(".git", ".engine", "node_modules"),
    )
    for rel, content in changes.items():
        p = destination / rel
        if content is None:
            p.unlink(missing_ok=True)
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(content)
    return destination


def save_patch(baseline: Path, candidate: Path, destination: Path):
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
        command(["git", "add", "-A"], cwd=trusted)
        patch = command(
            ["git", "diff", "--cached", "--binary", "--no-ext-diff", "HEAD", "--"],
            cwd=trusted,
        )
        destination.write_text(patch + ("\n" if patch else ""))


def write_task(path: Path, task: dict):
    import yaml

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(task, sort_keys=False, width=100))
    write_json(
        path.with_suffix(".prompt.json"), {"id": task["id"], "prompt": task["prompt"]}
    )
