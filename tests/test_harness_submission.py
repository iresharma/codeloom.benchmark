from harness.submission import pick_submission


def _tree(root, rel, text):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def test_engine_uses_newest_changed_worktree(tmp_path):
    baseline = tmp_path / "base"
    checkout = tmp_path / "checkout"
    worktrees = tmp_path / "worktrees"
    _tree(baseline, "src/app.py", "original")
    _tree(checkout, "src/app.py", "original")
    stale = worktrees / "old"
    fresh = worktrees / "new"
    _tree(stale, "src/app.py", "stale-edit")
    _tree(fresh, "src/app.py", "fresh-edit")
    import os
    import time

    now = time.time()
    os.utime(stale / "src/app.py", (now - 10, now - 10))
    os.utime(fresh / "src/app.py", (now, now))
    path, origin = pick_submission("engine-core", baseline, checkout, worktrees)
    assert path == fresh
    assert origin == "worktree:new"


def test_engine_falls_back_to_checkout_when_worktrees_match_baseline(tmp_path):
    baseline = tmp_path / "base"
    checkout = tmp_path / "checkout"
    worktrees = tmp_path / "worktrees"
    _tree(baseline, "src/app.py", "original")
    _tree(checkout, "src/app.py", "merged")
    _tree(worktrees / "idle", "src/app.py", "original")
    path, origin = pick_submission("engine-core", baseline, checkout, worktrees)
    assert path == checkout
    assert origin == "checkout"


def test_mini_always_uses_checkout(tmp_path):
    baseline = tmp_path / "base"
    checkout = tmp_path / "checkout"
    worktrees = tmp_path / "worktrees"
    _tree(baseline, "src/app.py", "original")
    _tree(checkout, "src/app.py", "mini-edit")
    _tree(worktrees / "coder", "src/app.py", "ignored")
    path, origin = pick_submission("mini-swe-agent", baseline, checkout, worktrees)
    assert path == checkout
    assert origin == "checkout"
