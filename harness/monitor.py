"""Local live monitor for a run directory. No cloud, no extra process in the campaign."""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import ROOT
from .status import discover_runs, log_tail, resolve_run, snapshot, trial_detail

PAGE = Path(__file__).with_name("static") / "monitor.html"


def serve(run_dir: Path | None = None, host="127.0.0.1", port=8765, open_browser=True):
    initial = run_dir.resolve() if run_dir else None
    state = {"run_dir": initial}
    allowed = [ROOT]
    if initial:
        allowed.append(initial)

    def current():
        path = state["run_dir"]
        if path is None:
            return {
                "waiting": True,
                "run_dir": "",
                "updated_at": time.time(),
                "tasks": [],
                "systems": [],
            }
        return snapshot(path)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            return

        def _send(self, status, body: bytes, content_type="application/json"):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, status, payload):
            return self._send(status, json.dumps(payload).encode())

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path in ("/", "/index.html"):
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            if parsed.path == "/api/status":
                return self._json(200, current())
            if parsed.path == "/api/runs":
                extra = state["run_dir"]
                return self._json(200, {"run_dir": str(extra or ""), "runs": discover_runs(ROOT, extra)})
            if parsed.path == "/api/events":
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                last = None
                try:
                    while True:
                        payload = json.dumps(current())
                        if payload != last:
                            self.wfile.write(b"data: " + payload.encode() + b"\n\n")
                            self.wfile.flush()
                            last = payload
                        time.sleep(1)
                except (BrokenPipeError, ConnectionResetError):
                    return
            if parsed.path.startswith("/api/trial/"):
                rest = parsed.path.removeprefix("/api/trial/")
                if state["run_dir"] is None:
                    return self._json(400, {"error": "no run open"})
                if rest.endswith("/log"):
                    ident = rest.removesuffix("/log")
                    query = parse_qs(parsed.query)
                    n = int(query.get("n", ["120"])[0])
                    try:
                        text = log_tail(state["run_dir"], ident, lines=min(max(n, 1), 400))
                    except ValueError:
                        return self._json(400, {"error": "invalid trial"})
                    return self._json(200, {"id": ident, "log": text})
                try:
                    return self._json(200, trial_detail(state["run_dir"], rest))
                except ValueError:
                    return self._json(400, {"error": "invalid trial"})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            parsed = urlparse(self.path)
            if parsed.path != "/api/open":
                return self._json(404, {"error": "not found"})
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                opened = resolve_run(Path(body["path"]), allowed)
            except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
                return self._json(400, {"error": str(exc) or "invalid run"})
            state["run_dir"] = opened
            return self._json(200, snapshot(opened))

    server = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{server.server_port}"
    label = state["run_dir"] or ROOT
    print(f"Monitor {label} at {url}", flush=True)
    if open_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMonitor stopped", flush=True)
    finally:
        server.server_close()
    return 0
