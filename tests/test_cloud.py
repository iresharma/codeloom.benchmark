import importlib.util
import io
import json
from pathlib import Path
import tarfile
import threading
import urllib.error
import urllib.request

import pytest


def bundle(files):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w:gz") as archive:
        for name, contents in files.items():
            contents = contents.encode()
            member = tarfile.TarInfo(name)
            member.size = len(contents)
            archive.addfile(member, io.BytesIO(contents))
    return output.getvalue()


@pytest.fixture
def monitor(tmp_path, monkeypatch):
    monkeypatch.setenv("MONITOR_UPLOAD_TOKEN", "test-upload-credential")
    spec = importlib.util.spec_from_file_location("monitor", Path(__file__).parents[1] / "cloud/monitor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.DATA = tmp_path
    server = module.ThreadingHTTPServer(("127.0.0.1", 0), module.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    yield module, url
    server.shutdown()
    server.server_close()


def post(url, endpoint, data, token="test-upload-credential"):
    request = urllib.request.Request(url + endpoint, data=data, headers={"Authorization": "Bearer " + token})
    return urllib.request.urlopen(request)


def test_atomic_publication_preserves_last_snapshot_after_rejected_upload(monitor):
    module, url = monitor
    payload = bundle({"summary.json": '{"status":"partial"}', "benchmarks/railway-v1/trials/a/patch.diff": "+valid"})
    assert post(url, "/api/upload/public", payload).status == 200
    with pytest.raises(urllib.error.HTTPError) as error:
        post(url, "/api/upload/public", bundle({"../private/exfiltrate": "bad"}))
    assert error.value.code == 400
    state = json.load(urllib.request.urlopen(url + "/api/status"))
    assert state["summary"]["status"] == "partial"
    assert urllib.request.urlopen(url + "/benchmarks/railway-v1/trials/a/patch.diff").read() == b"+valid"
    assert not (module.DATA / "private/exfiltrate").exists()


def test_private_ledger_backup_and_uploads_require_authentication(monitor):
    _, url = monitor
    backup = bundle({".cache/campaign/ledger.sqlite": "private token"})
    with pytest.raises(urllib.error.HTTPError) as error:
        post(url, "/api/upload/backup", backup, token="wrong")
    assert error.value.code == 401
    assert post(url, "/api/upload/backup", backup).status == 200
    for path in ("/api/backup", "/private/worker.tar.gz", "/benchmarks/../../private/worker.tar.gz"):
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(url + path)
    request = urllib.request.Request(url + "/api/backup", headers={"Authorization": "Bearer test-upload-credential"})
    assert urllib.request.urlopen(request).read() == backup


def test_sse_and_status_serve_the_same_persisted_worker_state(monitor):
    _, url = monitor
    assert post(url, "/api/upload/status", b'{"phase":"certifying","active":[]}').status == 200
    state = json.load(urllib.request.urlopen(url + "/api/status"))
    assert state["worker"]["received_at"] > 0
    with urllib.request.urlopen(url + "/api/events") as response:
        message = json.loads(response.readline().removeprefix(b"data: "))
        assert message == state
