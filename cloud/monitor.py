"""Persistent public projections and authenticated worker uploads. No inference here."""

import hashlib
import hmac
import io
import json
import mimetypes
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlsplit

DATA = Path(os.environ.get("DATA_DIR", "/data"))
TOKEN = os.environ["MONITOR_UPLOAD_TOKEN"]
LOCK = threading.RLock()


def unpack(data, destination):
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        members = archive.getmembers()
        if sum(m.size for m in members) > 512 * 1024 * 1024:
            raise ValueError("Archive too large")
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not member.isfile():
                raise ValueError("Unsafe archive member")
        archive.extractall(destination, members=members, filter="data")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def send(self, body, status=200, kind="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def authorized(self):
        return hmac.compare_digest(
            self.headers.get("Authorization", ""), "Bearer " + TOKEN
        )

    def do_POST(self):
        if not self.authorized():
            return self.send(b'{"error":"unauthorized"}', 401)
        path = urlsplit(self.path).path
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 256 * 1024 * 1024:
                return self.send(b'{"error":"size"}', 413)
            body = self.rfile.read(length)
            if path == "/api/upload/status":
                state = json.loads(body)
                state["received_at"] = time.time()
                with LOCK:
                    temp = DATA / "status.tmp"
                    temp.write_text(json.dumps(state))
                    temp.replace(DATA / "status.json")
            elif path == "/api/upload/public":
                with tempfile.TemporaryDirectory(dir=DATA) as folder:
                    stage = Path(folder)
                    unpack(body, stage)
                    # A full generation is promoted atomically; readers never see half an upload.
                    generation = DATA / (
                        "generation-" + hashlib.sha256(body).hexdigest()
                    )
                    if not generation.exists():
                        stage.rename(generation)
                    with LOCK:
                        temp = DATA / "current.tmp"
                        temp.symlink_to(generation.name)
                        temp.replace(DATA / "current")
                        for old in DATA.glob("generation-*"):
                            if old != generation:
                                shutil.rmtree(old)
            elif path == "/api/upload/backup":
                # Raw journals/ledger contain credentials and are never served publicly.
                private = DATA / "private"
                private.mkdir(mode=0o700, exist_ok=True)
                with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
                    archive.getmembers()  # reject corrupt/incomplete backups
                temp = private / "backup.tmp"
                temp.write_bytes(body)
                temp.chmod(0o600)
                temp.replace(private / "worker.tar.gz")
            else:
                return self.send(b'{"error":"not found"}', 404)
            self.send(b'{"ok":true}')
        except (ValueError, OSError, tarfile.TarError) as exc:
            self.send(json.dumps({"error": str(exc)}).encode(), 400)

    def snapshot(self):
        with LOCK:
            status = (
                json.loads((DATA / "status.json").read_text())
                if (DATA / "status.json").exists()
                else {"phase": "waiting", "message": "Waiting for the worker"}
            )
            result = {"worker": status, "summary": None, "setup": None, "runs": []}
            current = DATA / "current"
            for key, name in (
                ("summary", "summary.json"),
                ("setup", "setup.json"),
                ("runs", "runs.json"),
            ):
                if (current / name).exists():
                    result[key] = json.loads((current / name).read_text())
            return json.dumps(result).encode()

    def do_GET(self):
        path = unquote(urlsplit(self.path).path)
        if path == "/health":
            return self.send(b'{"ok":true}')
        if path in ("/", "/index.html"):
            return self.send(
                Path(__file__).with_name("dashboard.html").read_bytes(),
                kind="text/html; charset=utf-8",
            )
        if path == "/api/status":
            return self.send(self.snapshot())
        if path == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for _ in range(12):
                    self.wfile.write(b"data: " + self.snapshot() + b"\n\n")
                    self.wfile.flush()
                    time.sleep(5)
            except (BrokenPipeError, ConnectionResetError):
                pass
            self.close_connection = True
            return
        if path == "/api/backup" and self.authorized():
            source = DATA / "private/worker.tar.gz"
        elif path.startswith("/benchmarks/"):
            root = (DATA / "current/benchmarks").resolve()
            source = (root / path[len("/benchmarks/") :]).resolve()
            if not source.is_relative_to(root):
                return self.send(b'{"error":"not found"}', 404)
        else:
            return self.send(b'{"error":"not found"}', 404)
        with LOCK:
            if not source.is_file():
                return self.send(b'{"error":"not found"}', 404)
            self.send(
                source.read_bytes(),
                kind=mimetypes.guess_type(source.name)[0] or "application/octet-stream",
            )


if __name__ == "__main__":
    DATA.mkdir(parents=True, exist_ok=True)
    ThreadingHTTPServer(
        ("0.0.0.0", int(os.environ.get("PORT", "8080"))), Handler
    ).serve_forever()
