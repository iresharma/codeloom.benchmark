import gzip
import json
import pytest
from codeloom_benchmark.export import export_traces


def test_compressed_credentials_cannot_be_published(tmp_path):
    root = tmp_path / "run"
    trial = root / "trials" / "trial"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text("{}")
    (root / "blobs").mkdir()
    (root / "blobs/request.json.gz").write_bytes(
        gzip.compress(b'{"content":"sk-or-v1-synthetic-credential"}')
    )
    (trial / "broker.jsonl").write_text(
        json.dumps({"request_blob": "blobs/request.json.gz"}) + "\n"
    )
    with pytest.raises(ValueError, match="compressed evidence"):
        export_traces(root, tmp_path / "export")
    assert not (tmp_path / "export/blobs/request.json.gz").exists()


def test_unsealed_logs_are_withheld_while_attempt_stays_visible(tmp_path):
    root = tmp_path / "run"
    trial = root / "trials/interrupted"
    trial.mkdir(parents=True)
    (trial / "attempt.json").write_text(
        json.dumps(
            {
                "id": "interrupted",
                "task_id": "tinydb-B",
                "system": "mini-swe-agent",
                "stage": "measured",
                "retry_of": None,
            }
        )
    )
    (trial / "controller.log").write_text("sk-or-v1-synthetic-active-credential")
    target = tmp_path / "export"
    export_traces(root, target)
    result = json.loads((target / "trials/interrupted/result.json").read_text())
    assert result["termination"] == "infra_unresolved"
    assert result["cost_usd"] is None
    assert not (target / "trials/interrupted/controller.log").exists()
