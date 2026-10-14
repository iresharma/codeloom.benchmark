"""Invoke mini-SWE-agent's pinned controller without changing its loop or tool parser."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .artifacts import write_json


def scrub(value, token):
    if isinstance(value, dict):
        return {
            k: "<redacted>"
            if k.lower() in ("api_key", "authorization")
            else scrub(v, token)
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [scrub(v, token) for v in value]
    return value.replace(token, "<trial-token>") if isinstance(value, str) else value


def main():
    cfg = json.loads(sys.stdin.read())
    # Native retry utility and LiteLLM/OpenAI retries must not multiply paid requests.
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
        container_timeout=str(cfg["watchdog_s"] + 600) + "s",
        run_args=[
            "--platform",
            "linux/amd64",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--pids-limit=512",
            "--memory=3g",
            "--cpus=2",
            "--network",
            cfg["network"],
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
        write_json(root / "worker-result.json", scrub(result, cfg["token"]))
    finally:
        write_json(root / "native-trace.json", scrub(agent.serialize(), cfg["token"]))
        # Parent seals checkout before removing this container.
        env.container_id = None


if __name__ == "__main__":
    main()
