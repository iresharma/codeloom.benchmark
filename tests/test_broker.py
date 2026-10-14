import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import requests

from codeloom_benchmark.broker import Broker
from codeloom_benchmark.ledger import Ledger

SETTINGS = {
    "model": "test/model",
    "provider": "test/fp8",
    "max_tokens": 100,
    "reasoning_effort": "high",
    "input_per_million": 0.3,
    "output_per_million": 0.9,
}


def test_broker_pins_parameters_preserves_opaque_reasoning_and_refuses(tmp_path):
    seen = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            seen.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            payload = json.dumps(
                {
                    "model": "test/model",
                    "provider": "test",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "ok",
                                "reasoning_details": [
                                    {"type": "reasoning.encrypted", "data": "opaque"}
                                ],
                            }
                        }
                    ],
                    "usage": {"cost": 0.001, "completion_tokens": 4},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    ledger = Ledger(tmp_path / "ledger.db")
    token = ledger.create_trial("test", "measured", 1)
    broker = Broker(
        ledger,
        tmp_path,
        "REAL_KEY_NOT_LOGGED",
        SETTINGS,
        f"http://127.0.0.1:{upstream.server_port}",
    )
    port = broker.start()
    try:
        url = f"http://127.0.0.1:{port}/v1/chat/completions"
        r = requests.post(
            url,
            headers={"Authorization": "Bearer " + token},
            json={
                "model": "test/model",
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert r.status_code == 200
        assert (
            r.json()["choices"][0]["message"]["reasoning_details"][0]["data"]
            == "opaque"
        )
        assert seen[0]["model"] == "test/model"
        assert seen[0]["provider"]["only"] == ["test/fp8"]
        ledger.revoke("test")
        r = requests.post(
            url,
            headers={"Authorization": "Bearer " + token},
            json={
                "model": "test/model",
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert r.status_code == 402
        assert len(seen) == 1
    finally:
        broker.close()
        upstream.shutdown()
        upstream.server_close()
    assert (
        "REAL_KEY_NOT_LOGGED" not in (tmp_path / "trials/test/broker.jsonl").read_text()
    )
