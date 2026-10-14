import json
from types import SimpleNamespace
import pytest
from codeloom_benchmark.ledger import Ledger
from codeloom_benchmark.manifests import config, tasks
from codeloom_benchmark.runner import attempt


def test_host_interrupt_preserves_attempt_and_revokes_token(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    broker = SimpleNamespace(
        ledger=ledger,
        settings=config(),
        server=SimpleNamespace(server_address=("localhost", 1)),
    )
    network = SimpleNamespace(name="unused", url="http://unused/v1")

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt()

    monkeypatch.setattr("codeloom_benchmark.runner.start_container", interrupt)
    task = next(t for t in tasks() if t["id"] == "tinydb-B")
    with pytest.raises(KeyboardInterrupt):
        attempt(
            task,
            "engine-core",
            "unused",
            broker,
            network,
            tmp_path,
            "interrupt",
            "development",
            60,
            1,
        )
    trial = tmp_path / "trials/interrupt"
    assert (trial / "attempt.json").exists()
    result = json.loads((trial / "result.json").read_text())
    assert result["termination"] == "host_error"
    assert not result["verification"]["pass"]
    assert result["cost_usd"] == 0
    assert not ledger.active_trials()
