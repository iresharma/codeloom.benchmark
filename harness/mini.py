"""Host-side mini-SWE-agent controller. The shell runs in the task container."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from . import write_json
from .settings import PLATFORM


def _scrub(value, token):
    if isinstance(value, dict):
        return {
            key: "<redacted>" if key.lower() in ("api_key", "authorization") else _scrub(item, token)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_scrub(item, token) for item in value]
    return value.replace(token, "<trial-token>") if isinstance(value, str) else value


def main():
    cfg = json.loads(sys.stdin.read())
    runtime = cfg.get("runtime") or {}
    os.environ["MSWEA_MODEL_RETRY_STOP_AFTER_ATTEMPT"] = "1"
    os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
    from minisweagent.agents.default import DefaultAgent
    from minisweagent.config import get_config_path
    from minisweagent.environments.docker import DockerEnvironment
    from minisweagent.models.litellm_model import LitellmModel
    import yaml

    root = Path(cfg["root"])
    native = yaml.safe_load(get_config_path("mini").read_text())
    env = DockerEnvironment(
        image=cfg["image"],
        cwd="/task",
        timeout=120,
        container_timeout="infinity",
        run_args=[
            "--platform",
            PLATFORM,
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=512",
            f"--memory={runtime.get('memory_gb', 3)}g",
            f"--cpus={runtime.get('cpus', 2)}",
            "--network",
            cfg["network"],
            "-e",
            "ENGINE_RESEARCH_GATEWAY="
            + (cfg.get("research_url") or cfg["url"].removesuffix("/v1") + "/research"),
            "-e",
            "ENGINE_RESEARCH_TOKEN=" + cfg["token"],
        ],
    )
    write_json(root / "container.json", {"id": env.container_id})
    model = LitellmModel(
        model_name="openrouter/" + cfg["model"],
        cost_tracking="ignore_errors",
        model_kwargs={
            "api_base": cfg["url"],
            "api_key": cfg["token"],
            "timeout": cfg["request_timeout_s"],
            "num_retries": 0,
            "reasoning_effort": cfg["reasoning_effort"],
        },
    )
    native["agent"]["cost_limit"] = 0
    native["agent"]["step_limit"] = 0
    agent = DefaultAgent(model, env, **native["agent"])
    try:
        result = agent.run(
            cfg["prompt"],
            system="Linux",
            release="container kernel",
            version="linux/amd64 task runtime",
            machine="x86_64",
        )
        write_json(root / "worker-result.json", _scrub(result, cfg["token"]))
    finally:
        write_json(root / "native-trace.json", _scrub(agent.serialize(), cfg["token"]))
        env.container_id = None


if __name__ == "__main__":
    main()
