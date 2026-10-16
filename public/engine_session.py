"""In-container Engine session. Keep writer worktrees; do not merge them away."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path


async def drive(options, process, workspace=Path("/task"), startup_timeout=120, events_path=Path("/tmp/engine-events.jsonl")):
    sys.path.insert(0, "/opt/engine")
    from clients.headless import HeadlessError, wait_until_idle
    from protocol.codec import STREAM_LIMIT, decode_event, encode
    from protocol.commands import StartSession, SubmitUserMessage
    from protocol.events import (
        AgentStateChanged,
        ChatMessageAdded,
        ErrorOccurred,
        SnapshotReady,
    )

    deadline = time.monotonic() + startup_timeout
    while True:
        try:
            reader, writer = await asyncio.open_unix_connection(
                str(workspace / ".engine/engine.sock"), limit=STREAM_LIMIT
            )
            break
        except (FileNotFoundError, ConnectionRefusedError):
            if process.poll() is not None or time.monotonic() >= deadline:
                raise HeadlessError("engine startup failed")
            await asyncio.sleep(0.2)

    try:
        with events_path.open("w") as events:
            main_finished = False

            async def send(command):
                writer.write(encode(command))
                await writer.drain()

            async def get_event():
                line = await reader.readline()
                if not line:
                    raise HeadlessError("engine IPC disconnected")
                event = decode_event(line)
                events.write(line.decode())
                events.flush()
                return event

            def on_event(event):
                nonlocal main_finished
                if (
                    isinstance(event, AgentStateChanged)
                    and not event.agent_id
                    and event.state == "thinking"
                    and event.turn == 0
                ):
                    main_finished = False
                if (
                    isinstance(event, ChatMessageAdded)
                    and event.role == "assistant"
                    and not event.agent_id
                ):
                    main_finished = True

            await send(StartSession(workspace=str(workspace)))
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise HeadlessError("timed out waiting for SnapshotReady")
                event = await asyncio.wait_for(get_event(), timeout=remaining)
                if isinstance(event, ErrorOccurred):
                    raise HeadlessError(event.message)
                if isinstance(event, SnapshotReady):
                    break
            await send(
                SubmitUserMessage(
                    text=options["prompt"],
                    model=options["model"],
                    reasoning_effort=options["reasoning_effort"],
                )
            )
            await wait_until_idle(get_event, send, settle="keep", on_event=on_event)
            if not main_finished:
                raise HeadlessError("engine went idle without a final assistant reply")
    finally:
        writer.close()
        await writer.wait_closed()


def main():
    options = json.loads(sys.stdin.readline())
    with open("/tmp/engine-process.log", "w") as log:
        process = subprocess.Popen(
            [sys.executable, "/opt/engine/app.py", "/task"], stdout=log, stderr=log
        )
        try:
            asyncio.run(drive(options, process))
        except Exception as exc:
            print(f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
            print(json.dumps({"termination": "agent_crash"}), flush=True)
            return 3
        else:
            print(json.dumps({"termination": "completed"}), flush=True)
            return 0
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
