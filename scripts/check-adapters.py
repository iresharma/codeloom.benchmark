"""CPU-only native adapter smoke test with an explicitly synthetic local provider."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import threading

from codeloom_benchmark.broker import Broker
from codeloom_benchmark.ledger import Ledger
from codeloom_benchmark.manifests import config, tasks
from codeloom_benchmark.runner import attempt
from codeloom_benchmark.sandbox import Network, task_image
from codeloom_benchmark.artifacts import write_json, digest


SEEN = []
OPAQUE = [
    {
        "type": "reasoning.text",
        "text": "synthetic retained state",
        "format": "unknown",
        "index": 0,
    }
]


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        SEEN.append(body)
        has_tool = any(m["role"] == "tool" for m in body["messages"])
        native_mini = any(
            t["function"]["name"] == "bash" for t in body.get("tools", [])
        )
        name = "bash" if native_mini else "run_command"
        cmd = (
            "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
            if native_mini and has_tool
            else "pwd"
        )
        message = {
            "role": "assistant",
            "content": "Synthetic adapter check; no live model.",
        }
        if not has_tool:
            message["reasoning_details"] = OPAQUE
            message["reasoning"] = "synthetic retained state"
        if not has_tool or native_mini:
            message["tool_calls"] = [
                {
                    "id": "t1" if not has_tool else "t2",
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps({"command": cmd}),
                    },
                }
            ]
        common = {
            "id": "synthetic",
            "created": 1,
            "model": body["model"],
            "provider": "test",
            "usage": {
                "cost": 0,
                "prompt_tokens": 10,
                "completion_tokens": 1,
                "total_tokens": 11,
            },
        }
        if body.get("stream"):
            common["object"] = "chat.completion.chunk"
            delta = {
                **message,
                "tool_calls": [
                    {**t, "index": i}
                    for i, t in enumerate(message.get("tool_calls", []))
                ],
            }
            chunks = [
                dict(
                    common,
                    choices=[{"index": 0, "delta": delta, "finish_reason": None}],
                ),
                dict(
                    common,
                    choices=[
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop" if has_tool else "tool_calls",
                        }
                    ],
                ),
            ]
            payload = (
                "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
                + "data: [DONE]\n\n"
            )
            mime = "text/event-stream"
        else:
            common["object"] = "chat.completion"
            payload = json.dumps(
                dict(
                    common,
                    choices=[
                        {"index": 0, "message": message, "finish_reason": "tool_calls"}
                    ],
                )
            )
            mime = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.end_headers()
        self.wfile.write(payload.encode())


def main():
    import tempfile

    with tempfile.TemporaryDirectory(prefix="benchmark-adapter-check-") as tmp:
        root = Path(tmp)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        settings = config()
        settings.update(max_tokens=100, provider_name="test")
        ledger = Ledger(root / "ledger.sqlite")
        broker = Broker(
            ledger,
            root,
            "SYNTHETIC_NOT_REAL",
            settings,
            f"http://127.0.0.1:{server.server_port}",
        )
        port = broker.start("0.0.0.0")
        network = Network(port)
        results = []
        try:
            task = next(t for t in tasks() if t["id"] == "tinydb-X")
            image = task_image(task)
            fidelity = {}
            for arm in settings["systems"]:
                SEEN.clear()
                result = attempt(
                    task,
                    arm,
                    image,
                    broker,
                    network,
                    root,
                    "adapter-" + arm,
                    "development",
                    120,
                    0.1,
                )
                assert result["termination"] == "completed", result
                assert result["request_count"] == 2, result
                assert not result["verification"]["pass"], (
                    "Synthetic no-op must not pass a feature task"
                )
                assistant = next(
                    m for m in SEEN[-1]["messages"] if m["role"] == "assistant"
                )
                fidelity[arm] = (
                    assistant.get("reasoning_details") == OPAQUE
                    or assistant.get("reasoning_content") == "synthetic retained state"
                )
                results.append(result)
            destination = Path("reports/adapter-check")
            destination.mkdir(parents=True, exist_ok=True)
            from codeloom_benchmark.export import export_traces

            export_traces(root, destination)
            write_json(
                destination / "status.json",
                {
                    "status": "passed" if all(fidelity.values()) else "failed",
                    "transport_status": "passed",
                    "synthetic": True,
                    "live_model_calls": 0,
                    "wire_reasoning_state_retained": fidelity,
                    "runtime": json.loads((Path(".cache/runtime.json")).read_text()),
                    "adapter_fingerprint": {
                        "worker": digest(Path("codeloom_benchmark/worker.py")),
                        "runner": digest(Path("codeloom_benchmark/runner.py")),
                        "probe": digest(Path("scripts/check-adapters.py")),
                    },
                    "mini_backend": "native LiteLLM OpenRouter",
                    "required_field": "DeepSeek reasoning_content; opaque top-level equality is also accepted",
                    "results": results,
                },
            )
            from codeloom_benchmark.artifacts import checksum_tree

            checksum_tree(destination)
            print(
                "Native transport passed; wire reasoning fidelity: "
                + json.dumps(fidelity)
                + "; no live model calls."
            )
        finally:
            network.close()
            broker.close()
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
