"""Runtime image, per-task images, internal network, evaluator containers."""

from __future__ import annotations

import io
import shutil
import subprocess
import tarfile
import uuid
from pathlib import Path

from . import ROOT, run, write_json
from .settings import PLATFORM, RUNTIME_TAG, SOCAT_IMAGE
from .snapshot import repo_cache, snapshot


def _copy_engine(engine: Path, dest: Path) -> None:
    git = ["git", f"--git-dir={engine / '.git'}", f"--work-tree={engine}"]
    tracked = run([*git, "ls-files", "--cached", "--others", "--exclude-standard"], cwd=engine)
    dest.mkdir(parents=True, exist_ok=True)
    for rel in tracked.stdout.splitlines():
        source = engine / rel
        if not source.is_file() or rel.startswith(("tests/", "docs/", ".github/")):
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def prepare_runtime(engine: Path) -> dict:
    engine = engine.resolve()
    stage = ROOT / ".cache/docker/base"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    for name in ("Dockerfile", "engine-requirements.lock", ".dockerignore"):
        shutil.copy2(ROOT / name, stage / name)
    shutil.copytree(ROOT / "public", stage / "public")
    shutil.copytree(ROOT / "runtime-node", stage / "runtime-node")
    _copy_engine(engine, stage / "engine")
    run(
        ["docker", "build", "--platform", PLATFORM, "-t", RUNTIME_TAG, str(stage)],
        timeout=2400,
    )
    image = run(["docker", "image", "inspect", RUNTIME_TAG, "--format", "{{.Id}}"]).stdout.strip()
    record = {"image": image, "tag": RUNTIME_TAG, "engine": str(engine)}
    write_json(ROOT / ".cache/runtime.json", record)
    return record


def _build(tag: str, stage: Path, timeout=3600) -> str:
    run(["docker", "build", "--platform", PLATFORM, "-t", tag, str(stage)], timeout=timeout)
    return run(["docker", "image", "inspect", tag, "--format", "{{.Id}}"]).stdout.strip()


def _dependency_image(task: dict) -> str:
    if task["suite"] == "v2":
        return _dependency_v2(task)
    if task["language"] != "typescript":
        return RUNTIME_TAG
    stage = ROOT / ".cache/docker/dependencies" / task["repo"]
    stage.mkdir(parents=True, exist_ok=True)
    source = repo_cache(task)
    (stage / "package.json").write_bytes(
        subprocess.check_output(["git", "show", task["commit"] + ":package.json"], cwd=source)
    )
    lock = ROOT / "suites/v1/dependencies" / task["repo"] / "package-lock.json"
    if lock.exists():
        shutil.copy2(lock, stage / "package-lock.json")
        copy = "COPY package.json package-lock.json /task/"
        install = "npm ci --ignore-scripts --package-lock=true"
    else:
        copy = "COPY package.json /task/"
        install = "npm install --ignore-scripts --package-lock=true"
    (stage / "Dockerfile").write_text(
        f"FROM {RUNTIME_TAG}\nUSER root\n{copy}\n"
        f"RUN cd /task && {install} && npm cache clean --force && chown -R bench:bench /task\n"
        "USER bench\n"
    )
    tag = f"codeloom-harness-deps-{task['repo']}:v1"
    _build(tag, stage)
    if not lock.exists():
        cid = run(["docker", "create", "--platform", PLATFORM, tag]).stdout.strip()
        try:
            lock.parent.mkdir(parents=True, exist_ok=True)
            run(["docker", "cp", cid + ":/task/package-lock.json", str(lock)])
        finally:
            run(["docker", "rm", cid], check=False)
        return _dependency_image(task)
    return tag


def _prepare_memos_backend(task: dict) -> str:
    tag = f"codeloom-harness-backend-{task['repo']}:{task['commit'][:12]}"
    stage = ROOT / ".cache/docker/backend" / task["repo"]
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    target = stage / "source"
    target.mkdir()
    raw = subprocess.check_output(["git", "archive", task["commit"]], cwd=repo_cache(task))
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        archive.extractall(target, filter="data")
    (stage / "Dockerfile").write_text(
        f"FROM {RUNTIME_TAG}\nUSER root\nWORKDIR /build\n"
        "COPY source/go.mod source/go.sum ./\nRUN go mod download\n"
        "COPY source .\nRUN go build -o /memos ./bin/memos\n"
    )
    _build(tag, stage)
    return tag


def _dependency_v2(task: dict) -> str:
    root = ROOT / "suites" / task["suite"] / "dependencies" / task["repo"]
    tag = f"codeloom-harness-deps-{task['repo']}:v2"
    stage = ROOT / ".cache/docker/dependencies/v2" / task["repo"]
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    lines = [f"FROM {RUNTIME_TAG}", "USER root"]
    runtime = task.get("runtime") or {}
    if runtime.get("browser"):
        lines.append(
            "RUN PLAYWRIGHT_BROWSERS_PATH=/opt/browsers python -m playwright install --with-deps chromium"
        )
    if runtime.get("node_workdir") is not None:
        workdir = runtime["node_workdir"]
        package_path = (workdir + "/" if workdir else "") + "package.json"
        package = subprocess.check_output(
            ["git", "show", task["commit"] + ":" + package_path], cwd=repo_cache(task)
        ).decode()
        for change in task.get("setup", {}).get(package_path, []):
            if package.count(change["old"]) != 1:
                raise ValueError("Dependency manifest recipe drift")
            package = package.replace(change["old"], change["new"])
        (stage / "package.json").write_text(package)
        shutil.copy2(root / "package-lock.json", stage / "package-lock.json")
        target = "/task/" + workdir
        lines += [
            f"COPY package.json package-lock.json {target}/",
            f"RUN cd {target} && npm ci --ignore-scripts && npm rebuild esbuild && npm cache clean --force",
        ]
        if workdir:
            lines += [
                "RUN mkdir -p /opt/benchmark-dependencies && "
                f"mv {target}/node_modules /opt/benchmark-dependencies/node_modules && "
                f"ln -s /opt/benchmark-dependencies/node_modules {target}/node_modules && "
                "chown -R bench:bench /opt/benchmark-dependencies"
            ]
    lock = root / "requirements.lock"
    if lock.exists():
        shutil.copy2(lock, stage / "requirements.lock")
        lines += [
            "COPY requirements.lock /opt/benchmark/requirements.lock",
            "RUN pip install --no-cache-dir -r /opt/benchmark/requirements.lock",
        ]
    if task["repo"] in ("httpx", "starlette"):
        lines.append(f"RUN pip uninstall -y {task['repo']}")
    if runtime.get("go_modules"):
        for name in ("go.mod", "go.sum"):
            (stage / name).write_bytes(
                subprocess.check_output(
                    ["git", "show", task["commit"] + ":" + name], cwd=repo_cache(task)
                )
            )
        lines += ["COPY go.mod go.sum /task/", "RUN cd /task && go mod download"]
        backend = _prepare_memos_backend(task)
        lines.append(f"COPY --from={backend} /memos /opt/benchmark/memos")
    lines += ["RUN chown -R bench:bench /task /opt/go-modules /tmp", "USER bench"]
    (stage / "Dockerfile").write_text("\n".join(lines) + "\n")
    _build(tag, stage)
    return tag


def task_image(task: dict) -> str:
    parent = _dependency_image(task)
    tag = f"codeloom-harness-{task['id'].lower()}:{task['suite']}"
    stage = ROOT / ".cache/docker/tasks" / task["id"]
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    snapshot(task, stage / "task")
    if task["suite"] == "v2":
        prepare = (task.get("runtime") or {}).get("prepare", "true")
        dep_line = "RUN " + prepare
    elif task["language"] == "go":
        dep_line = (
            "RUN cd /task && go mod download && go test -run '^$' ./... "
            "&& chown -R bench:bench /tmp/go-build"
        )
    else:
        dep_line = ""
    (stage / "Dockerfile").write_text(
        f"FROM {parent}\nUSER root\nCOPY task /task\n{dep_line}\n"
        "RUN chown -R bench:bench /task && rm -rf /root/.cache /root/.npm /root/go\n"
        "ENV PYTHONPATH=/task/src:/task:/opt/engine PLAYWRIGHT_BROWSERS_PATH=/opt/browsers\n"
        "USER bench\nWORKDIR /task\n"
    )
    return _build(tag, stage, timeout=2400)


class Network:
    def __init__(self, broker_port: int):
        self.name = "bench-" + uuid.uuid4().hex[:12]
        self.gateway = self.name + "-gw"
        run(["docker", "network", "create", "--internal", self.name])
        run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.gateway,
                "--add-host=host.docker.internal:host-gateway",
                SOCAT_IMAGE,
                "TCP-LISTEN:8080,fork,reuseaddr",
                f"TCP:host.docker.internal:{broker_port}",
            ]
        )
        run(["docker", "network", "connect", self.name, self.gateway])

    @property
    def url(self):
        return f"http://{self.gateway}:8080/v1"

    def close(self):
        subprocess.run(["docker", "rm", "-f", self.gateway], capture_output=True)
        subprocess.run(["docker", "network", "rm", self.name], capture_output=True)


def start_container(image: str, network=None, env=None, evaluator=False, resources=None):
    resources = resources or {"cpus": 2, "memory_gb": 3}
    args = [
        "docker",
        "run",
        "-d",
        "--platform",
        PLATFORM,
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=512",
        f"--memory={resources.get('memory_gb', 3)}g",
        f"--cpus={resources.get('cpus', 2)}",
        "--network",
        network or "none",
    ]
    if evaluator:
        args += ["--cap-add=CHOWN", "--cap-add=FOWNER", "--cap-add=DAC_OVERRIDE"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    return run(args + [image, "sleep", "infinity"]).stdout.strip()


def execute(container: str, shell: str, timeout=1200):
    return subprocess.run(
        ["docker", "exec", "-w", "/task", container, "bash", "-lc", shell],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def replace_task(container: str, source: Path, node_workdir=None):
    run(
        [
            "docker",
            "exec",
            "--user",
            "root",
            container,
            "sh",
            "-c",
            "find /task -mindepth 1 -maxdepth 1 ! -name node_modules -exec rm -rf {} +",
        ]
    )
    run(["docker", "cp", str(source) + "/.", container + ":/task"])
    if node_workdir:
        if Path(node_workdir).is_absolute() or ".." in Path(node_workdir).parts:
            raise ValueError("Unsafe dependency workdir")
        target = "/task/" + node_workdir + "/node_modules"
        run(
            [
                "docker",
                "exec",
                "--user",
                "root",
                container,
                "ln",
                "-sfn",
                "/opt/benchmark-dependencies/node_modules",
                target,
            ]
        )
    run(["docker", "exec", "--user", "root", container, "chown", "-R", "bench:bench", "/task"])


def stop(container: str) -> None:
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)


def export_dir(container: str, src: str, dest: Path) -> bool:
    dest.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["docker", "cp", container + ":" + src, str(dest)], capture_output=True
    )
    return result.returncode == 0
