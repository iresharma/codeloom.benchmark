"""Native IPC controller. Runs inside the task container; contains no evaluator."""

import json
import select
import socket
import subprocess
import sys
import time


def main():
    options = json.loads(sys.stdin.readline())
    log = open("/tmp/engine-process.log", "w")
    process = subprocess.Popen(
        [sys.executable, "/opt/engine/app.py", "/task"], stdout=log, stderr=log
    )
    sock = socket.socket(socket.AF_UNIX)
    try:
        deadline = time.monotonic() + 120
        while True:
            try:
                sock.connect("/task/.engine/engine.sock")
                break
            except (FileNotFoundError, ConnectionRefusedError):
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("engine startup failed")
                time.sleep(0.2)

        def send(kind, **fields):
            sock.sendall((json.dumps({"type": kind, **fields}) + "\n").encode())

        send("StartSession", workspace="/task")
        send(
            "SubmitUserMessage",
            text=options["prompt"],
            model=options["model"],
            reasoning_effort=options["reasoning_effort"],
        )
        buffered = b""
        started = False
        main_finished = False
        idle = 0
        with open("/tmp/engine-events.jsonl", "w") as events:
            while True:
                send("RequestRunState")
                ready, _, _ = select.select([sock], [], [], 0.5)
                if not ready:
                    continue
                data = sock.recv(65536)
                if not data:
                    raise RuntimeError("engine IPC disconnected")
                buffered += data
                while b"\n" in buffered:
                    line, buffered = buffered.split(b"\n", 1)
                    event = json.loads(line)
                    events.write(json.dumps(event) + "\n")
                    events.flush()
                    kind = event["type"]
                    if (
                        kind == "AgentStateChanged"
                        and not event.get("agent_id")
                        and event.get("state") == "thinking"
                        and event.get("turn") == 0
                    ):
                        main_finished = False
                    if (
                        kind == "ChatMessageAdded"
                        and event.get("role") == "assistant"
                        and not event.get("agent_id")
                    ):
                        main_finished = True
                    if kind == "ChatMessageStarted" or (
                        kind == "AgentStateChanged"
                        and event.get("state") not in ("idle", "done")
                    ):
                        started = True
                    if kind == "UserPromptRequested":
                        if event["kind"] == "settlement":
                            answer = "merge"
                        elif "merge" in event.get("choices", []):
                            answer = "merge"
                        elif event["kind"] in (
                            "continue",
                            "turn_continue",
                        ) or "continue" in event.get("choices", []):
                            answer = "continue"
                        else:
                            answer = (
                                event.get("default")
                                or (
                                    event.get("choices")
                                    or ["Proceed using the task specification."]
                                )[0]
                            )
                        send("AnswerPrompt", prompt_id=event["prompt_id"], text=answer)
                    if kind == "RunStateReady":
                        idle = idle + 1 if started and event["idle"] else 0
                        if idle >= 3:
                            if not main_finished:
                                print(
                                    json.dumps({"termination": "agent_crash"}),
                                    flush=True,
                                )
                                raise SystemExit(3)
                            print(json.dumps({"termination": "completed"}), flush=True)
                            return
                time.sleep(0.3)
    finally:
        sock.close()
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        log.close()


if __name__ == "__main__":
    main()
