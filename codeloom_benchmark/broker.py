"""A constrained HTTP inference proxy, not an agent or a context processor."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import threading
import time
from decimal import Decimal, ROUND_CEILING
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

from .artifacts import canonical, write_json
from .ledger import BudgetRefused, Ledger


class Broker:
    def __init__(
        self,
        ledger: Ledger,
        root: Path,
        key: str,
        settings: dict,
        upstream="https://openrouter.ai/api/v1",
        model_metadata=None,
    ):
        self.ledger, self.root, self.key, self.settings, self.upstream = (
            ledger,
            root,
            key,
            settings,
            upstream,
        )
        self.lock = threading.Lock()
        self.model_metadata = model_metadata
        self.admission = threading.Condition()
        self.server = None

    def spec_for(self, model_id):
        """Pricing/validation settings for a requested model: the frozen
        generator, or one of a small allowlist of secondary models used by
        a system's own support agents (e.g. a tester sub-agent). Unlisted
        keys fall back to the primary study settings."""
        if model_id == self.settings["model"]:
            return self.settings
        entry = self.settings.get("secondary_models", {}).get(model_id)
        if entry is None:
            return None
        return {**self.settings, **entry, "model": model_id}

    def prepare(self, body: dict):
        allowed = {
            "messages",
            "tools",
            "tool_choice",
            "stream",
            "response_format",
            "model",
            "temperature",
            "top_p",
            "max_tokens",
            "reasoning",
            "reasoning_effort",
            "provider",
            "usage",
            "stream_options",
        }
        if set(body) - allowed:
            raise ValueError("Unsupported inference parameter")
        if not isinstance(body.get("messages"), list) or not body["messages"]:
            raise ValueError("Messages required")
        spec = self.spec_for(body.get("model"))
        if spec is None:
            raise ValueError(
                "Native model does not match the frozen generator or an approved secondary model"
            )
        supplied = [
            value
            for value in (
                body.get("reasoning_effort"),
                body.get("reasoning", {}).get("effort"),
            )
            if value is not None
        ]
        if not supplied or any(
            value != spec["reasoning_effort"] for value in supplied
        ):
            raise ValueError("Native reasoning effort must explicitly match the study")
        reasoning = body.get("reasoning", {})
        if set(reasoning) - {"effort", "enabled", "exclude"}:
            raise ValueError("Only frozen reasoning controls are admitted")
        enabled = spec.get("thinking_enabled", spec["reasoning_effort"] != "none")
        if (
            reasoning.get("exclude") is True
            or ("enabled" in reasoning and reasoning["enabled"] != enabled)
            or "max_tokens" in reasoning
        ):
            raise ValueError("Native thinking settings conflict with the study")
        # Text-only study: image/file/audio URLs are outside the priced reservation.
        for message in body["messages"]:
            content = message.get("content")
            if isinstance(content, list) and any(
                p.get("type") != "text" for p in content
            ):
                raise ValueError("Only text content is admitted")
        body = dict(body)
        body.pop("reasoning_effort", None)
        body.update(
            model=spec["model"],
            temperature=1.0,
            top_p=0.95,
            max_tokens=spec["max_tokens"],
            reasoning={
                "effort": spec["reasoning_effort"],
                "enabled": enabled,
                "exclude": False,
            },
            provider={
                "only": [spec["provider"]],
                "allow_fallbacks": False,
                "require_parameters": True,
                "max_price": {
                    "prompt": spec["input_per_million"],
                    "completion": spec["output_per_million"],
                },
            },
            usage={"include": True},
        )
        if body.get("stream"):
            body["stream_options"] = {"include_usage": True}
        # Byte length bounds text BPE token counts; extra margin covers model framing.
        upper_input = (
            len(
                canonical(
                    {"messages": body["messages"], "tools": body.get("tools", [])}
                )
            )
            + 4096
        )
        reserve = Decimal(upper_input) * Decimal(
            str(spec["input_per_million"])
        ) + Decimal(body["max_tokens"]) * Decimal(
            str(spec["output_per_million"])
        )
        return body, int(reserve.to_integral_value(rounding=ROUND_CEILING))

    def blob(self, data: bytes) -> str:
        sha = hashlib.sha256(data).hexdigest()
        p = self.root / "blobs" / (sha + ".json.gz")
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(gzip.compress(data, mtime=0))
        return str(p.relative_to(self.root))

    def record(self, trial: str, entry: dict):
        p = self.root / "trials" / trial / "broker.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        with self.lock, p.open("a") as f:
            f.write(json.dumps(entry, sort_keys=True) + "\n")

    def start(self, host="127.0.0.1", port=0):
        broker = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass  # never log Authorization headers or per-trial tokens

            def error(self, status, kind, detail):
                payload = canonical({"error": {"code": kind, "message": detail}})
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_GET(self):
                if self.path.split("?", 1)[0] not in ("/v1/models", "/api/v1/models"):
                    return self.error(
                        404, "route_denied", "Only the pinned catalog is available"
                    )
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                with broker.ledger.connection() as db:
                    active = db.execute(
                        "SELECT id FROM trials WHERE token=? AND active=1", (token,)
                    ).fetchone()
                if not active:
                    return self.error(
                        401, "token_denied", "Active attempt token required"
                    )
                if not broker.model_metadata:
                    return self.error(
                        503, "catalog_missing", "Pinned model metadata unavailable"
                    )
                payload = canonical({"data": [broker.model_metadata]})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                if self.path not in (
                    "/v1/chat/completions",
                    "/api/v1/chat/completions",
                ):
                    return self.error(
                        404, "route_denied", "Only chat completions are available"
                    )
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8_000_000:
                    return self.error(413, "payload_denied", "Invalid payload size")
                token = self.headers.get("Authorization", "").removeprefix("Bearer ")
                admission_started = time.monotonic()
                try:
                    body, amount = broker.prepare(json.loads(self.rfile.read(size)))
                    spec = broker.spec_for(body["model"])
                    while True:
                        try:
                            call_id, trial = broker.ledger.reserve(token, amount)
                            break
                        except BudgetRefused:
                            if (
                                not broker.ledger.pending_can_release_budget(
                                    token, amount
                                )
                                or time.monotonic() - admission_started
                                > broker.settings.get("request_timeout_s", 1800)
                            ):
                                raise
                            with broker.admission:
                                broker.admission.wait(timeout=1)
                except BudgetRefused as exc:
                    trial = broker.ledger.trial_for_token(token)
                    if trial:
                        broker.record(
                            trial,
                            {
                                # ledger.reserve() already gives every refusal
                                # its own distinct reason (budget_pending,
                                # overrun_halt, unknown_bills_halt, ...);
                                # using it directly as the status keeps that
                                # distinction in the journal instead of
                                # collapsing everything but call_cost_limit/
                                # token_denied into generic "budget_exhausted".
                                "status": exc.reason,
                                "refusal_reason": exc.reason,
                                "detail": str(exc),
                                "estimated_cost_usd": amount / 1_000_000,
                                "request_blob": broker.blob(canonical(body)),
                                "elapsed_s": 0,
                                "started_at": time.time(),
                            },
                        )
                    if exc.reason == "token_denied":
                        return self.error(401, "token_denied", str(exc))
                    return self.error(402, "budget_exhausted", str(exc))
                except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                    trial = broker.ledger.trial_for_token(token)
                    if trial:
                        broker.record(
                            trial,
                            {
                                "status": "request_denied",
                                "detail": str(exc),
                                "elapsed_s": 0,
                                "started_at": time.time(),
                            },
                        )
                    return self.error(400, "request_denied", str(exc))
                started = time.monotonic()
                record = {
                    "id": call_id,
                    "trial_id": trial,
                    "reserved_usd": amount / 1_000_000,
                    "budget_policy": broker.ledger.snapshot()["budget_policy"],
                    "request_blob": broker.blob(canonical(body)),
                    "started_at": time.time(),
                    "admission_queue_s": time.monotonic() - admission_started,
                }
                raw = bytearray()
                usage = None
                status = "unknown"
                first = None
                try:
                    response = requests.post(
                        broker.upstream + "/chat/completions",
                        json=body,
                        headers={"Authorization": "Bearer " + broker.key},
                        stream=True,
                        timeout=(30, broker.settings.get("request_timeout_s", 1800)),
                    )
                    record["http_status"] = response.status_code
                    self.send_response(response.status_code)
                    self.send_header(
                        "Content-Type",
                        response.headers.get("Content-Type", "application/json"),
                    )
                    self.send_header("Connection", "close")
                    self.end_headers()
                    disconnected = False
                    for chunk in response.iter_content(chunk_size=1024):
                        if chunk:
                            if first is None:
                                first = time.monotonic() - started
                            raw.extend(chunk)
                            if not disconnected:
                                try:
                                    self.wfile.write(chunk)
                                    self.wfile.flush()
                                except (BrokenPipeError, ConnectionResetError):
                                    disconnected = True
                    if body.get("stream"):
                        events = []
                        for line in raw.decode().splitlines():
                            if line.startswith("data: ") and line[6:] != "[DONE]":
                                event = json.loads(line[6:])
                                events.append(event)
                                if event.get("usage"):
                                    usage = event["usage"]
                        models = {e.get("model") for e in events if e.get("model")}
                        providers = {
                            e.get("provider") for e in events if e.get("provider")
                        }
                    else:
                        event = json.loads(raw)
                        usage = event.get("usage")
                        models, providers = (
                            {event.get("model")},
                            {event.get("provider")},
                        )
                    record.update(
                        returned_models=sorted(m for m in models if m),
                        returned_providers=sorted(p for p in providers if p),
                    )
                    record["rate_limit_headers"] = {
                        k: v
                        for k, v in response.headers.items()
                        if k.lower().startswith("x-ratelimit-")
                        or k.lower() == "retry-after"
                    }
                    status = (
                        "complete"
                        if response.ok
                        else "provider_error"
                        if response.status_code in (408, 429)
                        or response.status_code >= 500
                        else "request_error"
                    )
                    if response.ok and models - {None, spec["model"]}:
                        status = "model_mismatch"
                        broker.ledger.revoke(trial)
                    expected_provider = spec.get(
                        "provider_name", spec["provider"].split("/")[0]
                    )
                    if response.ok and providers - {None, expected_provider}:
                        status = "provider_mismatch"
                        broker.ledger.revoke(trial)
                except Exception as exc:
                    record["exception_type"] = type(exc).__name__
                    status = "transport_error"
                    # Errors may have been billed; never assume a disconnected call was free.
                finally:
                    cost = usage.get("cost") if usage else None
                    if cost is not None and (
                        not isinstance(cost, (int, float))
                        or not math.isfinite(cost)
                        or cost < 0
                    ):
                        cost = None
                    if (
                        cost is None
                        and status == "provider_error"
                        and record.get("http_status") in (408, 429)
                        and not usage
                    ):
                        # A pre-generation rejection (rate limit/timeout) returns no
                        # usage: nothing was produced upstream, so nothing was billed.
                        # Settle at $0 instead of leaving an unknown bill that would
                        # halt admission for every other trial until reconciled.
                        cost = 0
                    broker.ledger.settle(call_id, cost, status)
                    with broker.admission:
                        broker.admission.notify_all()
                    elapsed = time.monotonic() - started
                    record.update(
                        status=status,
                        usage=usage,
                        first_byte_s=first,
                        elapsed_s=elapsed,
                        response_blob=broker.blob(bytes(raw)),
                        generated_tokens_per_elapsed_s=(
                            usage.get("completion_tokens", 0) / elapsed
                            if usage
                            else None
                        ),
                    )
                    broker.record(trial, record)
                    self.close_connection = True

        self.server = ThreadingHTTPServer((host, port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self.server.server_address[1]

    def close(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        write_json(self.root / "budget.json", self.ledger.snapshot())
