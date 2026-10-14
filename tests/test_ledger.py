from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from codeloom_benchmark.ledger import BudgetRefused, Ledger, micros


def test_concurrent_children_cannot_multiply_budget(tmp_path):
    ledger = Ledger(tmp_path / "ledger.db")
    token = ledger.create_trial("one", "measured", "1")

    def reserve(_):
        try:
            return ledger.reserve(token, micros(".3"))[0]
        except BudgetRefused:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = [c for c in pool.map(reserve, range(8)) if c]
    assert len(admitted) == 3
    assert Decimal(str(ledger.snapshot()["spent_or_reserved_usd"])) == Decimal(".9")


def test_unknown_bill_and_restart_keep_reservation(tmp_path):
    path = tmp_path / "budget.db"
    first = Ledger(path)
    token = first.create_trial("trial", "measured", "1")
    call, _ = first.reserve(token, micros(".8"))
    first.settle(call, None, "disconnected")
    restarted = Ledger(path)
    with pytest.raises(BudgetRefused):
        restarted.reserve(token, micros(".3"))
    assert restarted.snapshot()["unknown_calls"] == 1


def test_overrun_revokes_every_trial(tmp_path):
    ledger = Ledger(tmp_path / "budget.db")
    a = ledger.create_trial("a", "measured", "1")
    b = ledger.create_trial("b", "measured", "1")
    call, _ = ledger.reserve(a, micros(".2"))
    ledger.settle(call, ".25")
    assert ledger.snapshot()["overrun"]
    with pytest.raises(BudgetRefused):
        ledger.reserve(b, micros(".1"))


def test_stage_limit_and_fixed_resume_ceiling(tmp_path):
    path = tmp_path / "budget.db"
    ledger = Ledger(path, stage_limits={"measured": "1"})
    a = ledger.create_trial("a", "measured", "1")
    # Larger per-attempt maxima must not expand the shared measurement envelope.
    b = ledger.create_trial("b", "measured", "4")
    call, _ = ledger.reserve(a, micros(".9"))
    ledger.settle(call, ".8")
    with pytest.raises(BudgetRefused):
        ledger.reserve(b, micros(".3"))
    with pytest.raises(ValueError):
        Ledger(path, campaign_limit="50")


def test_campaign_cannot_restart_measurement_in_another_directory(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.claim_run("measured", tmp_path / "original")
    ledger.claim_run("measured", tmp_path / "original")
    with pytest.raises(ValueError, match="resume its original directory"):
        ledger.claim_run("measured", tmp_path / "another")
