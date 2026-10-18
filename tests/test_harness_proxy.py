import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from harness.proxy import BudgetRefused, Proxy
from harness.settings import MODEL


def _chunked_upstream(body: bytes, content_type="application/json"):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *_):
            return

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            self.wfile.write(f"{len(body):X}\r\n".encode("ascii") + body + b"\r\n0\r\n\r\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_issue_and_revoke(tmp_path):
    proxy = Proxy("sk-test", tmp_path)
    token = proxy.issue("tinydb-B-engine-core", 1.0)
    assert proxy._trial(token)["cap"] == 1.0
    proxy.revoke(token)
    with pytest.raises(BudgetRefused):
        proxy._trial(token)


def test_budget_stop_and_call_gate(tmp_path):
    proxy = Proxy("sk-test", tmp_path)
    token = proxy.issue("t1", 1.0)
    trial = proxy._trial(token)
    body, estimate = proxy.prepare(
        {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "high"}
    )
    assert body["model"] == MODEL
    assert body["provider"]["only"] == ["baidu/fp8"]
    proxy.admit(trial, 0.5)
    proxy.charge("t1", 1.0)
    with pytest.raises(BudgetRefused, match="reached the cap"):
        proxy.admit(trial, 0.01)
    with pytest.raises(BudgetRefused, match="3x"):
        proxy.admit({"id": "t2", "cap": 1.0}, 10)


def test_unknown_model_is_rejected(tmp_path):
    proxy = Proxy("sk-test", tmp_path)
    with pytest.raises(BudgetRefused):
        proxy.prepare({"model": "openai/gpt-4", "messages": [{"role": "user", "content": "x"}]})


def test_proxy_rewrites_chunked_json_for_httpx(tmp_path):
    payload = (
        b"\n         \n"
        + json.dumps(
            {
                "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                "usage": {"cost": 0.001, "prompt_tokens": 10, "completion_tokens": 4},
            }
        ).encode()
    )
    upstream = _chunked_upstream(payload)
    proxy = Proxy("sk-test", tmp_path, upstream=f"http://127.0.0.1:{upstream.server_port}")
    token = proxy.issue("t1", 4.0)
    port = proxy.start()
    try:
        with httpx.Client(timeout=5) as client:
            response = client.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={"model": MODEL, "messages": [{"role": "user", "content": "hi"}]},
            )
        assert response.status_code == 200
        assert response.json()["choices"][0]["message"]["content"] == "ok"
        assert response.headers.get("content-length") == str(len(payload))
        assert "chunked" not in (response.headers.get("transfer-encoding") or "")
    finally:
        proxy.close()
        upstream.shutdown()


def test_proxy_rewrites_chunked_sse(tmp_path):
    payload = (
        b'data: {"choices":[{"delta":{"content":"hi"}}],"usage":{"cost":0.0}}\n\n'
        b"data: [DONE]\n\n"
    )
    upstream = _chunked_upstream(payload, content_type="text/event-stream")
    proxy = Proxy("sk-test", tmp_path, upstream=f"http://127.0.0.1:{upstream.server_port}")
    token = proxy.issue("t1", 4.0)
    port = proxy.start()
    try:
        with httpx.Client(timeout=5) as client:
            response = client.post(
                f"http://127.0.0.1:{port}/v1/chat/completions",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "model": MODEL,
                    "stream": True,
                    "messages": [{"role": "user", "content": "hi"}],
                },
            )
        assert response.status_code == 200
        assert "data:" in response.text
        assert response.headers.get("content-type", "").startswith("text/event-stream")
        assert response.headers.get("content-length") == str(len(payload))
    finally:
        proxy.close()
        upstream.shutdown()
