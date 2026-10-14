"""Fail closed before paid calls when required state cannot round-trip natively."""

from __future__ import annotations
import json
import subprocess
from pathlib import Path
from .artifacts import write_json, digest
from .manifests import ROOT, config
from .sandbox import BASE_TAG, PLATFORM

ENGINE_PROBE = r"""
import asyncio,json
from types import SimpleNamespace
from llm.openrouter import _result_from_stream
import runtime.server
from agents.agent_loop import AgentLoop
opaque=[{'type':'reasoning.text','text':'synthetic conformance state','index':0}]
async def chunks():
 yield SimpleNamespace(model='test',id='test',usage=None,choices=[SimpleNamespace(finish_reason='tool_calls',delta=SimpleNamespace(content='',reasoning='synthetic conformance state',reasoning_details=opaque,tool_calls=[SimpleNamespace(index=0,id='c1',function=SimpleNamespace(name='bash',arguments='{}'))]))])
async def probe():
 result=await _result_from_stream(chunks(),None,idle_s=10)
 loop=AgentLoop(SimpleNamespace(model='test'))
 async def noop(*args,**kwargs):return 'synthetic tool result'
 loop._execute_call=noop
 await loop._dispatch(result)
 return {'engine_stream_retains_reasoning_details':getattr(result,'reasoning_details',None)==opaque,'engine_history_retains_reasoning_details':loop._history[0].get('reasoning_details')==opaque}
print(json.dumps(asyncio.run(probe())))
"""


def check(engine: Path, destination: Path):
    runtime = json.loads((ROOT / ".cache/runtime.json").read_text())
    current_image = subprocess.check_output(
        ["docker", "image", "inspect", BASE_TAG, "--format", "{{.Id}}"], text=True
    ).strip()
    if (
        current_image != runtime["image"]
        or any(
            not (engine / rel).is_file() or digest(engine / rel) != sha
            for rel, sha in runtime["engine_files"].items()
        )
        or runtime.get("public_files")
        != {
            str(p.relative_to(ROOT / "public")): digest(p)
            for p in (ROOT / "public").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }
    ):
        raise ValueError(
            "Runtime does not match the engine checkout or public driver; rebuild with prepare-runtime before compatibility"
        )
    # Execute the exact pinned runtime, not the host's possibly different SDK/dependencies.
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            PLATFORM,
            "--network",
            "none",
            "-e",
            "PYTHONPATH=/opt/engine",
            BASE_TAG,
            "python",
            "-c",
            ENGINE_PROBE,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode:
        raise RuntimeError(
            "Compatibility probe could not run: " + result.stderr[-1500:]
        )
    checks = json.loads(result.stdout)
    runtime = json.loads((ROOT / ".cache/runtime.json").read_text())
    adapter_path = ROOT / "reports/adapter-check/status.json"
    expected = {
        "worker": digest(ROOT / "codeloom_benchmark/worker.py"),
        "runner": digest(ROOT / "codeloom_benchmark/runner.py"),
        "probe": digest(ROOT / "scripts/check-adapters.py"),
        "broker": digest(ROOT / "codeloom_benchmark/broker.py"),
        "settings": digest(ROOT / "configs/core.yaml"),
        "model": digest(ROOT / "configs/model.json"),
    }
    adapter = json.loads(adapter_path.read_text()) if adapter_path.exists() else {}
    if (
        adapter.get("runtime", {}).get("image") != runtime["image"]
        or adapter.get("adapter_fingerprint") != expected
    ):
        probe = subprocess.run(
            [__import__("sys").executable, str(ROOT / "scripts/check-adapters.py")],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
        if probe.returncode:
            raise RuntimeError(
                "Synthetic native wire check could not run: " + probe.stderr[-1500:]
            )
        adapter = json.loads(adapter_path.read_text())
    checks.update(
        {
            arm + "_wire_preserves_required_reasoning": retained
            for arm, retained in adapter["wire_reasoning_state_retained"].items()
        }
    )
    checks["native_effort_matches"] = adapter.get("native_effort_matches", False)
    passed = all(checks.values())
    record = {
        "status": "passed" if passed else "failed",
        "model": config()["model"],
        "reasoning_effort": config()["reasoning_effort"],
        "paid_calls": 0,
        "checks": checks,
        "reason": "Native reasoning-state continuation conformance passed."
        if passed
        else "Engine streaming/history drops provider-required reasoning state; measured calls are disabled.",
        "sources": [
            "https://api-docs.deepseek.com/guides/thinking_mode/",
            "https://openrouter.ai/docs/guides/best-practices/reasoning-tokens",
        ],
        "policy": "Both native adapters must explicitly select the same model/effort; the broker rejects mismatches and never reconstructs histories.",
        "engine_source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=engine, text=True
        ).strip(),
        "runtime": runtime,
        "wire_evidence": "adapter-check/status.json",
    }
    write_json(destination, record)
    return record
