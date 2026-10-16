import subprocess
from codeloom_benchmark.fixtures import apply_recipe, snapshot, save_patch, output_changes
from codeloom_benchmark.manifests import tasks


def test_apply_recipe_creates_a_brand_new_file_via_an_empty_old_anchor(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    apply_recipe(
        root,
        {"pkg/new_module.py": [{"old": "", "new": "VALUE = 1\n"}]},
    )
    assert (root / "pkg" / "new_module.py").read_text() == "VALUE = 1\n"


def test_patch_includes_committed_changes_new_files_and_deletions(tmp_path):
    task = next(t for t in tasks() if t["id"] == "tinydb-X")
    base = snapshot(task, tmp_path / "base")
    candidate = snapshot(task, tmp_path / "candidate")
    target = candidate / "tinydb/database.py"
    target.write_text(target.read_text() + "\n# a submitted change\n")
    (candidate / "tinydb/version.py").unlink()
    (candidate / "tinydb/new_file.py").write_text("VALUE = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=candidate, check=True)
    subprocess.run(["git", "commit", "-qm", "agent commit"], cwd=candidate, check=True)
    patch = tmp_path / "patch.diff"
    save_patch(base, candidate, patch)
    assert "deleted file mode" in patch.read_text()
    assert "new file mode" in patch.read_text()
    assert str(tmp_path) not in patch.read_text()
    subprocess.run(["git", "apply", str(patch)], cwd=base, check=True)
    assert (base / "tinydb/new_file.py").read_text() == "VALUE = 1\n"
    assert not (base / "tinydb/version.py").exists()


def test_native_tests_are_allowed_but_configuration_edits_are_disallowed(tmp_path):
    task = next(t for t in tasks() if t["id"] == "tinydb-B")
    base = snapshot(task, tmp_path / "base")
    candidate = snapshot(task, tmp_path / "candidate")
    (candidate / "tests/test_new.py").write_text("def test_feature(): pass\n")
    config = candidate / "setup.cfg"
    config.write_text("# outside allowed paths\n")
    changes, bad = output_changes(base, candidate, task)
    assert "tests/test_new.py" in changes
    assert bad == ["setup.cfg"]


def test_stock_dotenv_links_are_sealable_but_new_links_are_rejected(tmp_path):
    from codeloom_benchmark.fixtures import snapshot, output_changes
    from codeloom_benchmark.manifests import tasks

    task = next(t for t in tasks() if t["id"] == "dotenv-T")
    baseline = snapshot(task, tmp_path / "baseline")
    candidate = snapshot(task, tmp_path / "candidate")
    license_path = baseline / "docs/license.md"
    assert license_path.is_file() and not license_path.is_symlink()
    assert output_changes(baseline, candidate, task) == ({}, [])
    (candidate / "benchmark_tests").mkdir()
    (candidate / "benchmark_tests/link.py").symlink_to(candidate / "src/dotenv/main.py")
    import pytest

    with pytest.raises(ValueError, match="symlink forbidden"):
        output_changes(baseline, candidate, task)
