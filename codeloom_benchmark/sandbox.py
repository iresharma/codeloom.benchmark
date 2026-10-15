from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from pathlib import Path

from .artifacts import write_json, digest, canonical
from .fixtures import command, snapshot, repo_source
from .manifests import ROOT

PLATFORM = "linux/amd64"
BASE_TAG = "codeloom-benchmark-runtime:dev"


def prepare_runtime(engine: Path):
    # Engine's own source is deliberately NOT copied into the build context: it
    # is bind-mounted into /opt/engine at container start instead (see
    # start_container). That decouples the base/task images -- and every
    # certified task's cache -- from Engine's commit entirely, so iterating on
    # Engine no longer forces every task to recertify from scratch. Only
    # engine-requirements.lock (actual runtime dependencies) is still baked in;
    # a dependency change is a genuine runtime change and should still rebuild.
    engine = engine.resolve()
    git = [
        "git",
        f"--git-dir={engine / '.git'}",
        f"--work-tree={engine}",
    ]
    tracked = command([*git, "ls-files"], cwd=engine).splitlines()
    stage = ROOT / ".cache/docker/base"
    stage.mkdir(parents=True, exist_ok=True)
    for rel in ("Dockerfile", "engine-requirements.lock", ".dockerignore"):
        shutil.copy2(ROOT / rel, stage / rel)
    shutil.copytree(ROOT / "public", stage / "public", dirs_exist_ok=True)
    shutil.copytree(ROOT / "runtime-node", stage / "runtime-node", dirs_exist_ok=True)
    command(
        ["docker", "build", "--platform", PLATFORM, "-t", BASE_TAG, str(stage)],
        timeout=2400,
    )
    image = command(["docker", "image", "inspect", BASE_TAG, "--format", "{{.Id}}"])
    engine_files = {
        rel: digest(engine / rel)
        for rel in tracked
        if (engine / rel).is_file()
    }
    record = {
        "engine_files": engine_files,
        "public_files": {
            str(p.relative_to(ROOT / "public")): digest(p)
            for p in (ROOT / "public").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        },
        "platform": PLATFORM,
        "image": image,
        "engine_commit": command([*git, "rev-parse", "HEAD"], cwd=engine),
        "engine_dirty": bool(command([*git, "status", "--porcelain"], cwd=engine)),
    }
    write_json(ROOT / ".cache/runtime.json", record)
    return record


def dependency_image(task: dict):
    if task["language"] != "typescript":
        return BASE_TAG
    stage = ROOT / ".cache/docker/dependencies" / task["repo"]
    stage.mkdir(parents=True, exist_ok=True)
    source = repo_source(task)
    (stage / "package.json").write_bytes(
        subprocess.check_output(
            ["git", "show", task["commit"] + ":package.json"], cwd=source
        )
    )
    deps = ROOT / "suites/v1/dependencies" / task["repo"]
    deps.mkdir(parents=True, exist_ok=True)
    lock = deps / "package-lock.json"
    if lock.exists():
        shutil.copy2(lock, stage / "package-lock.json")
        copy = "COPY package.json package-lock.json /task/"
        install = "npm ci --ignore-scripts --package-lock=true"
    else:
        (stage / "package-lock.json").unlink(missing_ok=True)
        copy = "COPY package.json /task/"
        install = "npm install --ignore-scripts --package-lock=true"
    extra = (
        " && PLAYWRIGHT_BROWSERS_PATH=/opt/browsers npx playwright install --with-deps chromium"
        if task["repo"] == "ky"
        else ""
    )
    dockerfile = f"""FROM {BASE_TAG}
USER root
{copy}
RUN cd /task && {install}{extra} && npm cache clean --force && chown -R bench:bench /task
USER bench
"""
    (stage / "Dockerfile").write_text(dockerfile)
    tag = "codeloom-benchmark-deps-" + task["repo"] + ":v1"
    command(
        ["docker", "build", "--platform", PLATFORM, "-t", tag, str(stage)], timeout=2400
    )
    if not lock.exists():
        cid = command(["docker", "create", "--platform", PLATFORM, tag])
        try:
            command(["docker", "cp", cid + ":/task/package-lock.json", str(lock)])
        finally:
            command(["docker", "rm", cid])
        return dependency_image(task)
    return tag


def task_image(task: dict):
    parent = dependency_image(task)
    tag = "codeloom-benchmark-" + task["id"].lower() + ":v1"
    stage = ROOT / ".cache/docker/tasks" / task["id"]
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    snapshot(task, stage / "task")
    dep_line = (
        "RUN cd /task && go mod download && go test -run '^$' ./... && chown -R bench:bench /tmp/go-build"
        if task["language"] == "go"
        else ""
    )
    runtime = json.loads((ROOT / ".cache/runtime.json").read_text())
    task_hash = __import__("hashlib").sha256(canonical(task)).hexdigest()
    (stage / "Dockerfile").write_text(f"""FROM {parent}
USER root
COPY task /task
{dep_line}
RUN chown -R bench:bench /task && rm -rf /root/.cache /root/.npm /root/go
ENV PYTHONPATH=/task/src:/task:/opt/engine PLAYWRIGHT_BROWSERS_PATH=/opt/browsers
LABEL org.codeloom.runtime-image="{runtime["image"]}" org.codeloom.task-fingerprint="{task_hash}"
USER bench
WORKDIR /task
""")
    command(
        ["docker", "build", "--platform", PLATFORM, "-t", tag, str(stage)], timeout=2400
    )
    return command(["docker", "image", "inspect", tag, "--format", "{{.Id}}"])


def audit_image(image: str, task: dict):
    # Don't install target packages in runtime site-packages or leave downloaded archive copies.
    sentinel = {
        "tinydb": "tinydb",
        "dotenv": "dotenv",
        "ky": "ky",
        "pqueue": "p-queue",
        "chi": "chi",
    }[task["repo"]]
    import hashlib

    source = repo_source(task)
    known = {}
    for rel in command(
        ["git", "ls-tree", "-r", "--name-only", task["commit"]], cwd=source
    ).splitlines():
        if Path(rel).suffix not in (".py", ".go", ".ts"):
            continue
        raw = subprocess.check_output(
            ["git", "show", task["commit"] + ":" + rel], cwd=source
        )
        if len(raw) >= 1024:
            known.setdefault(str(len(raw)), []).append(hashlib.sha256(raw).hexdigest())
    script = r"""
import os,json,sys,hashlib
target=sys.argv[1];known=json.loads(sys.argv[2]);findings=[]
for root,dirs,files in os.walk('/'):
 if root.startswith(('/proc','/sys','/dev','/task/.git')):dirs[:]=[];continue
 for name in dirs:
  if name==target and not (root=='/task' or (target=='dotenv' and root=='/task/src')):
   findings.append(os.path.join(root,name))
 for name in files:
  path=os.path.join(root,name)
  if (not root.startswith('/task') or root.startswith('/task/node_modules')) and not os.path.islink(path):
   try:
    size=str(os.stat(path).st_size)
    if size in known and hashlib.sha256(open(path,'rb').read()).hexdigest() in known[size]:findings.append(path)
   except (OSError,PermissionError):pass
  if name.endswith(('.whl','.tgz','.tar.gz','.zip')) and target.lower() in name.lower():findings.append(os.path.join(root,name))
  if root.startswith('/opt') and name in ('rubric.json','check_test.go','check.ts','check.py'):findings.append(os.path.join(root,name))
print(json.dumps({'extra_target_copies':findings,'status':'pass' if not findings else 'fail'}))
sys.exit(bool(findings))
"""
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            PLATFORM,
            "--network",
            "none",
            image,
            "python",
            "-c",
            script,
            sentinel,
            json.dumps(known),
        ],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode:
        raise RuntimeError("Image leakage audit failed: " + result.stdout[-2000:])
    return json.loads(result.stdout)


class Network:
    """Internal agent network with a single fixed inference relay; no Docker socket mounts."""

    def __init__(self, broker_port: int):
        self.name = "bench-" + uuid.uuid4().hex[:12]
        self.gateway = self.name + "-gateway"
        command(["docker", "network", "create", "--internal", self.name])
        command(
            [
                "docker",
                "run",
                "-d",
                "--name",
                self.gateway,
                "--add-host=host.docker.internal:host-gateway",
                "alpine/socat:1.8.0.3@sha256:beb4a68d9e4fe6b0f21ea774a0fde6c31f580dde6368939ed70100c5385b015e",
                "TCP-LISTEN:8080,fork,reuseaddr",
                f"TCP:host.docker.internal:{broker_port}",
            ]
        )
        command(["docker", "network", "connect", self.name, self.gateway])

    @property
    def url(self):
        return f"http://{self.gateway}:8080/v1"

    def close(self):
        subprocess.run(["docker", "rm", "-f", self.gateway], capture_output=True)
        subprocess.run(["docker", "network", "rm", self.name], capture_output=True)


def start_container(
    image: str,
    network=None,
    ipc: Path | None = None,
    env=None,
    evaluator=False,
    engine: Path | None = None,
):
    args = [
        "docker",
        "run",
        "-d",
        "--platform",
        PLATFORM,
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--pids-limit=512",
        "--memory=3g",
        "--cpus=2",
        "--network",
        network or "none",
    ]
    if ipc:
        ipc.mkdir(parents=True, exist_ok=True)
        args += ["--mount", f"type=bind,source={ipc},target=/task/.engine"]
    if engine is not None:
        # Engine is bind-mounted, not baked into the image (see
        # prepare_runtime), so nothing stops the checkout from drifting
        # between certify-time and run-time the way the image digest used
        # to. Enforce the same guarantee explicitly: the mounted commit
        # must match what was certified, and must be clean.
        engine = engine.resolve()
        runtime = json.loads((ROOT / ".cache/runtime.json").read_text())
        git = ["git", f"--git-dir={engine / '.git'}", f"--work-tree={engine}"]
        commit = command([*git, "rev-parse", "HEAD"], cwd=engine)
        if commit != runtime["engine_commit"]:
            raise RuntimeError(
                "Engine checkout has moved since certification "
                f"(certified {runtime['engine_commit']}, now {commit}); "
                "recertify before running"
            )
        if command([*git, "status", "--porcelain"], cwd=engine):
            raise RuntimeError(
                "Engine checkout must stay clean to match the certified commit"
            )
        args += ["--mount", f"type=bind,source={engine},target=/opt/engine,readonly"]
    if evaluator:
        args += ["--cap-add=CHOWN", "--cap-add=FOWNER", "--cap-add=DAC_OVERRIDE"]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    return command(args + [image, "sleep", "infinity"])


def execute(container, shell, timeout=900):
    return subprocess.run(
        ["docker", "exec", "-w", "/task", container, "bash", "-lc", shell],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def replace_task(container: str, source: Path):
    # Evaluators use clean image dependencies but reconstruct source; trust no agent-installed tools.
    command(
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
    command(["docker", "cp", str(source) + "/.", container + ":/task"])
    command(
        [
            "docker",
            "exec",
            "--user",
            "root",
            container,
            "chown",
            "-R",
            "bench:bench",
            "/task",
        ]
    )


def remove(container):
    subprocess.run(["docker", "rm", "-f", container], capture_output=True)
