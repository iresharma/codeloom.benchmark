"""Exercise the adapter against the actual client in the sibling Engine checkout."""

import asyncio
import importlib.util
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest


spec = importlib.util.spec_from_file_location(
    "engine_driver", Path(__file__).parents[1] / "public/engine_driver.py"
)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


@pytest.fixture
def native_client(monkeypatch):
    checkout = Path(
        os.environ.get("ENGINE_CHECKOUT", Path(__file__).parents[2] / "engine")
    )
    if not (checkout / "clients/headless.py").is_file():
        pytest.skip("requires the pinned Engine checkout (set ENGINE_CHECKOUT)")
    monkeypatch.syspath_prepend(str(checkout))
    return pytest.importorskip(
        "clients.headless",
        reason="requires Engine runtime dependencies (engine-requirements.lock)",
        exc_type=ModuleNotFoundError,
    )


async def exercise(script, events_path):
    # Keep the Unix socket path below macOS's length limit.
    with tempfile.TemporaryDirectory(prefix="cl-driver-", dir="/tmp") as directory:
        workspace = Path(directory)
        (workspace / ".engine").mkdir()
        handled = asyncio.get_running_loop().create_future()
        received = []

        async def handle(reader, writer):
            async def recv():
                command = json.loads(await reader.readline())
                received.append(command)
                return command

            async def emit(event_type, **fields):
                writer.write((json.dumps({"type": event_type, **fields}) + "\n").encode())
                await writer.drain()

            try:
                assert (await recv())["type"] == "StartSession"
                # Submission before the asynchronous session snapshot is unsafe.
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(reader.readline(), timeout=0.05)
                await emit(
                    "SnapshotReady",
                    snapshot={
                        "session_id": "s",
                        "workspace": str(workspace),
                        "git": {},
                    },
                )
                assert await recv() == {
                    "type": "SubmitUserMessage",
                    "text": "fix the feature",
                    "model": "test/model",
                    "reasoning_effort": "low",
                }
                await script(emit, recv, workspace)
                assert await reader.read() == b""
                handled.set_result(None)
            except BaseException as exc:
                handled.set_exception(exc)
            finally:
                writer.close()
                await writer.wait_closed()

        server = await asyncio.start_unix_server(
            handle, str(workspace / ".engine/engine.sock")
        )
        try:
            await driver.drive(
                {
                    "prompt": "fix the feature",
                    "model": "test/model",
                    "reasoning_effort": "low",
                },
                Mock(poll=lambda: None),
                workspace=workspace,
                events_path=events_path,
            )
        finally:
            await asyncio.wait_for(handled, timeout=5)
            server.close()
            await server.wait_closed()
        return received


def test_native_post_child_grace_preserves_delayed_followup(native_client, tmp_path):
    async def script(emit, recv, workspace):
        await emit("AgentStateChanged", state="thinking", turn=0, max_turns=16)
        await emit(
            "ChatMessageAdded",
            id="spawn",
            role="assistant",
            text="Started coder",
            ts="",
        )
        await emit("AgentStateChanged", state="idle", turn=0, max_turns=16)
        await emit(
            "AgentFinished",
            agent_id="coder",
            profile="coder",
            status="ok",
            summary="fixed",
        )
        await emit("AgentsUpdated", agents=[])
        # Longer than the old driver's 1.5s completion threshold.
        await asyncio.sleep(2)
        await emit("AgentStateChanged", state="thinking", turn=0, max_turns=16)
        await emit(
            "UserPromptRequested",
            prompt_id="p",
            kind="confirm",
            question="Run tests?",
            choices=[],
        )
        assert await recv() == {"type": "AnswerPrompt", "prompt_id": "p", "text": "yes"}
        (workspace / "verified.txt").write_text("follow-up completed")
        await emit(
            "ChatMessageAdded", id="done", role="assistant", text="Verified", ts=""
        )
        await emit("AgentStateChanged", state="idle", turn=0, max_turns=16)

    path = tmp_path / "events.jsonl"
    received = asyncio.run(exercise(script, path))
    assert [c["type"] for c in received] == [
        "StartSession",
        "SubmitUserMessage",
        "AnswerPrompt",
    ]
    assert any(
        json.loads(line).get("id") == "done" for line in path.read_text().splitlines()
    )


def test_error_after_busy_is_reported_as_crash(native_client, tmp_path):
    async def script(emit, recv, workspace):
        await emit("AgentStateChanged", state="thinking", turn=0, max_turns=16)
        await emit("ErrorOccurred", message="llm failed")
        await emit("AgentStateChanged", state="idle", turn=0, max_turns=16)

    with pytest.raises(
        native_client.HeadlessError, match="without a final assistant reply"
    ):
        asyncio.run(exercise(script, tmp_path / "events.jsonl"))


def test_native_prompt_policy_covers_current_and_legacy_prompts(native_client):
    from protocol.events import UserPromptRequested

    def answer(kind, choices):
        return native_client.auto_answer(
            UserPromptRequested(
                prompt_id="p", question="Proceed?", kind=kind, choices=choices
            ),
            settle="merge",
        )

    assert answer("confirm", []) == "yes"
    assert answer("choice", ["continue", "handoff", "stop"]) == "continue"
    assert answer("choice", ["merge", "pr", "keep", "discard"]) == "merge"
    assert answer("mcp_auth", []) == "no"
    with pytest.raises(native_client.HeadlessError, match="interactive input"):
        answer("interview", [])


def test_main_reports_json_and_cleans_up_after_failure(monkeypatch, capsys):
    import io

    process = Mock()
    monkeypatch.setattr(driver.sys, "stdin", io.StringIO('{"prompt": "x"}'))
    monkeypatch.setattr(driver.subprocess, "Popen", Mock(return_value=process))

    async def fail(*args, **kwargs):
        raise RuntimeError("disconnected")

    monkeypatch.setattr(driver, "drive", fail)
    assert driver.main() == 3
    assert json.loads(capsys.readouterr().out) == {"termination": "agent_crash"}
    process.terminate.assert_called_once()
    process.wait.assert_called_once_with(timeout=10)
