"""Collect the tree that should be graded.

mini-SWE-agent edits /task. Engine writers edit linked checkouts under
/task/.engine/worktrees/<agent_id>. The old runner copied /task and skipped
.engine, so an unmerged worktree looked empty.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from .snapshot import IGNORED, output_changes


def copy_tree(source: Path, destination: Path) -> Path:
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        rel = path.relative_to(source)
        if any(part in IGNORED for part in rel.parts):
            continue
        target = destination / rel
        if path.is_symlink():
            raise ValueError(f"Output symlink forbidden: {rel}")
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        elif path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    return destination


def pick_submission(system: str, baseline: Path, checkout: Path, worktrees: Path | None):
    """Return (path, origin). origin is checkout or worktree:<agent_id>."""
    if system == "engine-core" and worktrees and worktrees.exists():
        best = None
        newest = -1.0
        for tree in sorted(p for p in worktrees.iterdir() if p.is_dir()):
            changes, _ = output_changes(baseline, tree, {"allowed_paths": ["*"]})
            if not changes:
                continue
            mtimes = [p.stat().st_mtime for p in tree.rglob("*") if p.is_file()]
            stamp = max(mtimes) if mtimes else tree.stat().st_mtime
            if stamp >= newest:
                newest = stamp
                best = tree
        if best is not None:
            return best, f"worktree:{best.name}"
    return checkout, "checkout"
