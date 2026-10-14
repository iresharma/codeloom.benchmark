import json
import pytest
from codeloom_benchmark.cli import schedule, run
from codeloom_benchmark.manifests import ROOT, tasks, task_limit, validate_suite
from codeloom_benchmark.ledger import Ledger, BudgetRefused


def test_schedule_is_complete_paired_and_order_balanced():
    rows = schedule()
    assert len(rows) == 20
    assert len({(r["task_id"], r["system"]) for r in rows}) == 20
    assert {r["task_id"] for r in rows} == {t["id"] for t in tasks()}
    pairs = [rows[i : i + 2] for i in range(0, 20, 2)]
    assert all(p[0]["task_id"] == p[1]["task_id"] for p in pairs)
    assert sum(p[0]["system"] == "engine-core" for p in pairs) == 5
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
    assert report["pairs"]["unrun"] == 10
    assert report["blocked_reason"] == "Native compatibility failed"


def test_price_overrun_cannot_be_reset_by_new_trial(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    token = ledger.create_trial("one", "development", 1)
    call, _ = ledger.reserve(token, 1000)
    ledger.settle(call, 0.002)
    with pytest.raises(BudgetRefused):
        ledger.create_trial("two", "development", 1)


def test_selected_difficulty_mix_and_paired_caps_preserve_shared_budget():
    from collections import Counter
    from decimal import Decimal
    from codeloom_benchmark.manifests import config

    selected = validate_suite()
    assert Counter(t["difficulty"] for t in selected) == {
        "light": 3,
        "medium": 3,
        "heavy": 4,
    }
    assert {t["repo"] for t in selected} == {"tinydb", "dotenv", "pqueue", "chi"}
    assert {t["category"] for t in selected} == {
        "bug",
        "cross-file",
        "navigation",
        "test-generation",
    }
    caps = sum(Decimal(str(task_limit(t))) * 2 for t in selected)
    assert caps == Decimal("50")
    assert config()["stage_limits"]["measured"] == 32
    assert config()["campaign_limit_usd"] == 45
    assert isinstance(config()["paid_runs_authorized"], bool)
