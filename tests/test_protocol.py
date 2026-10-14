import json
import pytest
from codeloom_benchmark.cli import schedule, run
from codeloom_benchmark.manifests import ROOT, tasks
from codeloom_benchmark.ledger import Ledger, BudgetRefused


def test_schedule_is_complete_paired_and_order_balanced():
    rows = schedule()
    assert len(rows) == 32
    assert len({(r["task_id"], r["system"]) for r in rows}) == 32
    assert {r["task_id"] for r in rows} == {t["id"] for t in tasks()}
    pairs = [rows[i : i + 2] for i in range(0, 32, 2)]
    assert all(p[0]["task_id"] == p[1]["task_id"] for p in pairs)
    assert sum(p[0]["system"] == "engine-core" for p in pairs) == 8
    assert rows == schedule()


def test_gate_stops_before_credentials_and_keeps_truthful_report(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "codeloom_benchmark.cli.check",
        lambda *args: {"status": "failed", "reason": "Native compatibility failed"},
    )
    monkeypatch.setattr(
        "codeloom_benchmark.campaign.credentials",
        lambda: pytest.fail("credentials read despite failed gate"),
    )
    root = tmp_path / "blocked"
    with pytest.raises(ValueError, match="Native compatibility failed"):
        run(root, ROOT.parent / "engine")
    report = json.loads((root / "summary.json").read_text())
    assert report["status"] == "not_run"
    assert report["pairs"]["unrun"] == 16
    assert report["blocked_reason"] == "Native compatibility failed"


def test_price_overrun_cannot_be_reset_by_new_trial(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    token = ledger.create_trial("one", "development", 1)
    call, _ = ledger.reserve(token, 1000)
    ledger.settle(call, 0.002)
    with pytest.raises(BudgetRefused):
        ledger.create_trial("two", "development", 1)
