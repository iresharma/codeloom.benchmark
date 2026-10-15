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
        assert r.status_code == 401
        assert r.json()["error"]["code"] == "token_denied"
        assert len(seen) == 1
    finally:
        broker.close()
        upstream.shutdown()
        upstream.server_close()
    assert (
        "REAL_KEY_NOT_LOGGED" not in (tmp_path / "trials/test/broker.jsonl").read_text()
    )
    assert (
        json.loads(
            (tmp_path / "trials/test/broker.jsonl").read_text().splitlines()[-1]
        )["status"]
        == "token_denied"
    )


def test_rate_limit_with_no_usage_settles_free_and_does_not_poison_other_trials(
    tmp_path,
):
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.dumps(
                {"error": {"message": "rate limited", "code": 429}}
            ).encode()
            self.send_response(429)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    ledger = Ledger(tmp_path / "ledger.db")
    rate_limited = ledger.create_trial("rate-limited", "measured", 1)
    other = ledger.create_trial("other", "measured", 1)
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
            headers={"Authorization": "Bearer " + rate_limited},
            json={
                "model": "test/model",
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert r.status_code == 429
        record = json.loads(
            (tmp_path / "trials/rate-limited/broker.jsonl").read_text().splitlines()[
                -1
            ]
        )
        assert record["status"] == "provider_error"
        assert ledger.snapshot()["unknown_calls"] == 0
        # A pre-generation rate limit on one trial must never block another.
        r = requests.post(
            url,
            headers={"Authorization": "Bearer " + other},
            json={
                "model": "test/model",
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": "hello"}],
            },
        )
        assert r.status_code == 429
    finally:
        broker.close()
        upstream.shutdown()
        upstream.server_close()


def test_journal_records_the_specific_refusal_reason_not_generic_budget_exhausted(
    tmp_path, monkeypatch
):
    # A same-trial in-flight-bill race (budget_pending) is not a real budget
    # exhaustion; the journal must say so distinctly, not collapse it into
    # the generic "budget_exhausted" string the way it used to.
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(500)
            self.end_headers()

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
    # Force the admission retry loop to give up on its first attempt,
    # deterministically, instead of depending on real request timing.
    monkeypatch.setattr(Ledger, "pending_can_release_budget", lambda *a, **k: False)

    def fake_reserve(self, tok, amount):
        from codeloom_benchmark.ledger import BudgetRefused

        raise BudgetRefused(
            "Waiting for this trial's in-flight bill", "budget_pending"
        )

    monkeypatch.setattr(Ledger, "reserve", fake_reserve)
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
        assert r.status_code == 402
        record = json.loads(
            (tmp_path / "trials/test/broker.jsonl").read_text().splitlines()[-1]
        )
        assert record["status"] == "budget_pending"
    finally:
        broker.close()
        upstream.shutdown()
        upstream.server_close()
