import json
from pathlib import Path
from unittest.mock import Mock

from codeloom_benchmark import cli


def test_runtime_only_command_does_not_certify_or_start_paid_calls(monkeypatch, capsys):
    prepare = Mock(return_value={"image": "new-runtime", "engine_commit": "new-engine"})
    monkeypatch.setattr(cli, "prepare_runtime", prepare)
    forbidden = Mock(side_effect=AssertionError("runtime preparation must only build"))
    monkeypatch.setattr(cli, "certification", forbidden)
    monkeypatch.setattr(cli, "check", forbidden)
    from codeloom_benchmark import campaign

    monkeypatch.setattr(campaign, "calibrate", forbidden)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["benchmark", "prepare-runtime", "--engine", "/checkout/engine"],
    )
    cli.main()
    prepare.assert_called_once_with(Path("/checkout/engine"))
    forbidden.assert_not_called()
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary["status"] == "ready"


def test_runtime_uses_target_engine_when_git_environment_points_elsewhere(
    tmp_path, monkeypatch
):
    import subprocess
    from codeloom_benchmark import sandbox

    def repo(path, filename):
        path.mkdir()
        (path / filename).write_text("content\n")
        argv = ["git", f"--git-dir={path / '.git'}", f"--work-tree={path}"]
        for args in [
            ["init"],
            ["config", "user.email", "test@example.invalid"],
            ["config", "user.name", "Test"],
            ["add", filename],
            ["commit", "--no-verify", "-m", "initial"],
        ]:
            subprocess.run([*argv, *args], check=True, capture_output=True)
        return subprocess.check_output([*argv, "rev-parse", "HEAD"], text=True).strip()

    engine, unrelated = tmp_path / "engine", tmp_path / "unrelated"
    expected_commit = repo(engine, "app.py")
    repo(unrelated, "wrong.py")
    root = tmp_path / "benchmark"
    root.mkdir()
    for name in ("Dockerfile", "engine-requirements.lock", ".dockerignore"):
        (root / name).write_text("")
    for name in ("public", "runtime-node"):
        (root / name).mkdir()
    monkeypatch.setattr(sandbox, "ROOT", root)
    monkeypatch.setenv("GIT_DIR", str(unrelated / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(unrelated))
    original = sandbox.command

    def command(argv, **kwargs):
        if argv[0] == "docker":
            return "sha256:runtime" if argv[1:3] == ["image", "inspect"] else ""
        return original(argv, **kwargs)

    monkeypatch.setattr(sandbox, "command", command)
    # Engine's source is bind-mounted at container start, not copied into the
    # build context (see start_container) -- so the real guard here is that
    # the recorded commit/files/dirty state reflect the explicit `engine`
    # argument, not whatever GIT_DIR/GIT_WORK_TREE happen to point at.
    runtime = sandbox.prepare_runtime(engine)
    assert runtime["engine_commit"] == expected_commit
    assert set(runtime["engine_files"]) == {"app.py"}
    assert runtime["engine_dirty"] is False


def _git_repo(path):
    import subprocess

    path.mkdir(parents=True, exist_ok=True)
    (path / "app.py").write_text("one\n")
    argv = ["git", f"--git-dir={path / '.git'}", f"--work-tree={path}"]
    for args in [
        ["init"],
        ["config", "user.email", "test@example.invalid"],
        ["config", "user.name", "Test"],
        ["add", "app.py"],
        ["commit", "--no-verify", "-m", "initial"],
    ]:
        subprocess.run([*argv, *args], check=True, capture_output=True)
    return subprocess.check_output([*argv, "rev-parse", "HEAD"], text=True).strip()


def _write_runtime_json(root, engine_commit):
    import json

    cache = root / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    (cache / "runtime.json").write_text(json.dumps({"engine_commit": engine_commit}))


def test_start_container_refuses_an_engine_checkout_that_moved_since_certification(
    tmp_path, monkeypatch
):
    from codeloom_benchmark import sandbox

    engine = tmp_path / "engine"
    certified_commit = _git_repo(engine)
    _write_runtime_json(tmp_path / "benchmark", certified_commit)
    monkeypatch.setattr(sandbox, "ROOT", tmp_path / "benchmark")
    (engine / "app.py").write_text("two\n")
    subprocess_run = __import__("subprocess").run
    subprocess_run(
        [
            "git",
            f"--git-dir={engine / '.git'}",
            f"--work-tree={engine}",
            "commit",
            "-am",
            "moved",
        ],
        check=True,
        capture_output=True,
    )
    try:
        sandbox.start_container("unused-image", engine=engine)
    except RuntimeError as exc:
        assert "moved since certification" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_start_container_refuses_a_dirty_engine_checkout(tmp_path, monkeypatch):
    from codeloom_benchmark import sandbox

    engine = tmp_path / "engine"
    certified_commit = _git_repo(engine)
    _write_runtime_json(tmp_path / "benchmark", certified_commit)
    monkeypatch.setattr(sandbox, "ROOT", tmp_path / "benchmark")
    (engine / "app.py").write_text("uncommitted change\n")
    try:
        sandbox.start_container("unused-image", engine=engine)
    except RuntimeError as exc:
        assert "clean" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_start_container_mounts_a_clean_matching_engine_checkout(tmp_path, monkeypatch):
    from codeloom_benchmark import sandbox

    engine = tmp_path / "engine"
    certified_commit = _git_repo(engine)
    _write_runtime_json(tmp_path / "benchmark", certified_commit)
    monkeypatch.setattr(sandbox, "ROOT", tmp_path / "benchmark")
    captured = {}
    original = sandbox.command

    def command(argv, **kwargs):
        if argv[:2] == ["docker", "run"]:
            captured["argv"] = argv
            return "container-id"
        return original(argv, **kwargs)

    monkeypatch.setattr(sandbox, "command", command)
    sandbox.start_container("unused-image", engine=engine)
    mount_index = captured["argv"].index("--mount")
    assert f"source={engine.resolve()},target=/opt/engine,readonly" in captured[
        "argv"
    ][mount_index + 1]
