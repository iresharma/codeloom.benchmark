from harness.snapshot import apply_recipe, output_changes


def test_apply_recipe_replaces_and_writes(tmp_path):
    source = tmp_path / "pkg.py"
    source.write_text("alpha\nkeep\n")
    apply_recipe(tmp_path, {"pkg.py": [{"old": "alpha", "new": "beta", "count": 1}]})
    apply_recipe(tmp_path, {"notes.txt": "hello\n"})
    assert source.read_text() == "beta\nkeep\n"
    assert (tmp_path / "notes.txt").read_text() == "hello\n"


def test_output_changes_respects_allowed_paths(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    (before / "src").mkdir(parents=True)
    (after / "src").mkdir(parents=True)
    (before / "src/a.py").write_text("one")
    (after / "src/a.py").write_text("two")
    (before / "locked.txt").write_text("same")
    (after / "locked.txt").write_text("changed")
    changes, disallowed = output_changes(
        before, after, {"allowed_paths": ["src/*.py"]}
    )
    assert "src/a.py" in changes
    assert disallowed == ["locked.txt"]
