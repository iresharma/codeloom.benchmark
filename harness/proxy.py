"""Host-only OpenRouter relay. Agents never see the real key."""

from __future__ import annotations

import gzip
import hashlib
import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import requests

from .settings import (
    CALL_LIMIT_MULTIPLIER,
    INPUT_PER_MILLION,
    MAX_TOKENS,
    MODEL,
    OUTPUT_PER_MILLION,
    PROVIDER,
    REASONING_EFFORT,
    SECONDARY_MODELS,
)

UPSTREAM = "https://openrouter.ai/api/v1"


class BudgetRefused(Exception):
    def __init__(self, code, detail):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class Proxy:
    def __init__(self, key: str, run_dir: Path, upstream: str = UPSTREAM):
        self.key = key
        self.run_dir = run_dir
        self.upstream = upstream.rstrip("/")
        self.lock = threading.Lock()
        self.trials = {}
        self.spent = {}
        self.research_counts = {}
        self.server = None
        self.port = None

    def issue(self, trial_id: str, cap: float, research=None) -> str:
        token = secrets.token_urlsafe(24)
        with self.lock:
            self.trials[token] = {
                "id": trial_id,
                "cap": cap,
                "research": research or {},
                "revoked": False,
            }
            self.spent.setdefault(trial_id, 0.0)
        return token

    def revoke(self, token: str) -> None:
        with self.lock:
            if token in self.trials:
                self.trials[token]["revoked"] = True

    def cost(self, trial_id: str) -> float:
        with self.lock:
            return self.spent.get(trial_id, 0.0)

    def _trial(self, token: str) -> dict:
        row = self.trials.get(token)
        if not row or row["revoked"]:
            raise BudgetRefused("auth", "Unknown or revoked trial token")
        return row

    def _spec(self, model: str) -> dict | None:
        if model == MODEL:
            return {
                "model": MODEL,
                "provider": PROVIDER,
                "reasoning_effort": REASONING_EFFORT,
                "input_per_million": INPUT_PER_MILLION,
                "output_per_million": OUTPUT_PER_MILLION,
                "max_tokens": MAX_TOKENS,
                "thinking_enabled": True,
            }
        extra = SECONDARY_MODELS.get(model)
        if extra:
            return {"model": model, **extra, "max_tokens": extra.get("max_tokens", MAX_TOKENS)}
        return None

    def prepare(self, body: dict) -> tuple[dict, float]:
        spec = self._spec(body.get("model", MODEL))
        if spec is None:
            raise BudgetRefused("model_mismatch", "Model is not on the study allowlist")
        body = dict(body)
        body.pop("reasoning_effort", None)
        body.update(
            model=spec["model"],
            temperature=1.0,
            top_p=0.95,
            max_tokens=spec["max_tokens"],
            reasoning={
                "effort": spec["reasoning_effort"],
                "enabled": spec.get("thinking_enabled", True),
                "exclude": False,
            },
            provider={
                "only": [spec["provider"]],
                "allow_fallbacks": False,
                "require_parameters": True,
            },
            usage={"include": True},
        )
        if body.get("stream"):
            body["stream_options"] = {"include_usage": True}
        estimate = (
            len(json.dumps({"messages": body.get("messages", []), "tools": body.get("tools", [])}))
            + 4096
        ) / 1_000_000 * spec["input_per_million"] + spec["max_tokens"] / 1_000_000 * spec[
            "output_per_million"
        ]
        return body, estimate

    def admit(self, trial: dict, estimate: float) -> None:
        cap = trial["cap"]
        spent = self.spent.get(trial["id"], 0.0)
        if estimate > CALL_LIMIT_MULTIPLIER * cap:
            raise BudgetRefused("call_cost_limit", "Call estimate exceeds 3x the attempt cap")
        if spent >= cap:
            raise BudgetRefused("budget_exhausted", "Attempt spend has reached the cap")

    def _blob(self, data: bytes) -> str:
        digest = hashlib.sha256(data).hexdigest()
        path = self.run_dir / "blobs" / (digest + ".json.gz")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_bytes(gzip.compress(data, mtime=0))
        return str(path.relative_to(self.run_dir))

    def record(self, trial_id: str, entry: dict) -> None:
        path = self.run_dir / "trials" / trial_id / "broker.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, path.open("a") as handle:
            handle.write(json.dumps(entry, sort_keys=True) + "\n")

    def charge(self, trial_id: str, dollars: float) -> None:
        with self.lock:
            self.spent[trial_id] = self.spent.get(trial_id, 0.0) + dollars

    def start(self, host="127.0.0.1", port=0):
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_):
                return

            def _write(self, status, body: bytes, content_type="application/json"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()

            def error(self, status, code, detail):
                payload = json.dumps({"error": {"code": code, "message": detail}}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def token(self):
                return self.headers.get("Authorization", "").removeprefix("Bearer ").strip()

            def do_GET(self):
                path = self.path.split("?", 1)[0]
                if path not in ("/v1/models", "/api/v1/models"):
                    return self.error(404, "route_denied", "Only the pinned catalog is available")
                try:
                    proxy._trial(self.token())
                except BudgetRefused as exc:
                    return self.error(401, exc.code, exc.detail)
                payload = json.dumps(
                    {
                        "data": [
                            {
                                "id": MODEL,
                                "object": "model",
                                "owned_by": "deepseek",
                            }
                        ]
                    }
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                path = self.path.split("?", 1)[0]
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                token = self.token()
                try:
                    trial = proxy._trial(token)
                except BudgetRefused as exc:
                    return self.error(401, exc.code, exc.detail)
                if path in ("/research", "/v1/research"):
                    return self.research(trial, raw)
                if path not in ("/v1/chat/completions", "/chat/completions"):
                    return self.error(404, "route_denied", "Only chat completions are available")
                try:
                    body, estimate = proxy.prepare(json.loads(raw))
                    with proxy.lock:
                        proxy.admit(trial, estimate)
                except BudgetRefused as exc:
                    status = 402 if exc.code in {"budget_exhausted", "call_cost_limit"} else 400
                    proxy.record(
                        trial["id"],
                        {"id": secrets.token_hex(8), "status": exc.code, "detail": exc.detail},
                    )
                    return self.error(status, exc.code, exc.detail)
                except (ValueError, json.JSONDecodeError) as exc:
                    return self.error(400, "request_denied", str(exc))
                started = time.monotonic()
                try:
                    response = requests.post(
                        proxy.upstream + "/chat/completions",
                        json=body,
                        headers={
                            "Authorization": "Bearer " + proxy.key,
                            "Content-Type": "application/json",
                        },
                        stream=True,
                        timeout=2400,
                    )
                    chunks = []
                    try:
                        for chunk in response.iter_content(8192):
                            if chunk:
                                chunks.append(chunk)
                    finally:
                        response.close()
                    payload = b"".join(chunks)
                    status = "complete" if response.status_code < 400 else "provider_error"
                except requests.RequestException as exc:
                    proxy.record(
                        trial["id"],
                        {"id": secrets.token_hex(8), "status": "transport_error", "detail": str(exc)},
                    )
                    return self.error(502, "transport_error", str(exc))
                usage = _usage(payload)
                cost = _cost_usd(usage, body["model"])
                proxy.charge(trial["id"], cost)
                proxy.record(
                    trial["id"],
                    {
                        "id": secrets.token_hex(8),
                        "status": status,
                        "elapsed_s": time.monotonic() - started,
                        "usage": usage,
                        "cost_usd": cost,
                        "request_blob": proxy._blob(json.dumps(body).encode()),
                        "response_blob": proxy._blob(payload),
                    },
                )
                try:
                    self._write(
                        response.status_code,
                        payload,
                        response.headers.get("Content-Type", "application/json"),
                    )
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    return

            def research(self, trial, raw):
                from .research import handle as research_handle

                folder = proxy.run_dir / "trials" / trial["id"] / "research"
                try:
                    result = research_handle(
                        folder,
                        json.loads(raw),
                        trial["research"],
                        proxy.research_counts,
                        trial["id"],
                    )
                except (ValueError, json.JSONDecodeError) as exc:
                    return self.error(400, "research_denied", str(exc))
                payload = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.port

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


def _usage(payload: bytes) -> dict:
    text = payload.decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
        return data.get("usage") or {}
    except json.JSONDecodeError:
        last = {}
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            piece = line[5:].strip()
            if piece == "[DONE]":
                continue
            try:
                chunk = json.loads(piece)
            except json.JSONDecodeError:
                continue
            if chunk.get("usage"):
                last = chunk["usage"]
        return last


def _cost_usd(usage: dict, model: str) -> float:
    if usage.get("cost") is not None:
        return float(usage["cost"])
    spec = SECONDARY_MODELS.get(model)
    inp = INPUT_PER_MILLION if spec is None else spec["input_per_million"]
    out = OUTPUT_PER_MILLION if spec is None else spec["output_per_million"]
    prompt = float(usage.get("prompt_tokens") or 0)
    completion = float(usage.get("completion_tokens") or 0)
    return prompt / 1_000_000 * inp + completion / 1_000_000 * out


def research_policy(task: dict) -> dict:
    extra = task.get("research") or {}
    refs = dict(extra.get("repo_refs") or {})
    parsed = urlparse(task.get("upstream", ""))
    if parsed.netloc == "github.com":
        owner_repo = parsed.path.strip("/").removesuffix(".git")
        refs.setdefault(owner_repo, task["commit"])
    return {"repo_refs": refs, "hosts": list(extra.get("hosts") or [])}
