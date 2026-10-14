from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from codeloom_benchmark import campaign
from codeloom_benchmark.ledger import BudgetRefused, Ledger, micros


def test_matched_pair_overlaps_and_drains_before_return(monkeypatch):
    monkeypatch.setattr(campaign, "config", lambda: {"parallel_attempts": 2})
    barrier = threading.Barrier(2)
    finished = set()

    def execute(system):
        barrier.wait(timeout=2)  # serial execution cannot reach this barrier
        finished.add(system)
        return system

    assert campaign.parallel_pair(["mini", "engine"], execute) == ["mini", "engine"]
    assert finished == {"mini", "engine"}


def test_unrelated_inflight_call_does_not_make_completed_trial_bill_unknown(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    a = ledger.create_trial("completed", "measured", "1")
    b = ledger.create_trial("running", "measured", "1")
    call_a, _ = ledger.reserve(a, micros(".2"))
    call_b, _ = ledger.reserve(b, micros(".2"))
    ledger.settle(call_a, ".01")
    assert ledger.unknown_calls_for_trial("completed") == 0
    assert ledger.unknown_calls_for_trial("running") == 1
    assert ledger.snapshot()["unknown_calls"] == 1
    ledger.settle(call_b, ".01")


def test_unknown_bill_halts_other_arm_even_with_remaining_budget(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    a = ledger.create_trial("a", "measured", "1")
    b = ledger.create_trial("b", "measured", "1")
    unknown, _ = ledger.reserve(a, micros(".1"))
    pending, _ = ledger.reserve(b, micros(".1"))
    ledger.settle(unknown, None, "transport_error")
    with pytest.raises(BudgetRefused, match="unknown bills"):
        ledger.reserve(b, micros(".1"))
    assert not ledger.pending_can_release_budget(b, micros(".1"))
    ledger.settle(pending, ".01")


def test_wrong_provider_revokes_both_parallel_arms(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    a = ledger.create_trial("a", "measured", "1")
    b = ledger.create_trial("b", "measured", "1")
    call, _ = ledger.reserve(a, micros(".1"))
    ledger.settle(call, ".01", "provider_mismatch")
    assert ledger.snapshot()["protocol_violation"]
    with pytest.raises(BudgetRefused, match="identity mismatch"):
        ledger.reserve(b, micros(".1"))
    with pytest.raises(BudgetRefused, match="identity mismatch"):
        ledger.create_trial("late-arm", "measured", "1")


def test_parallel_arms_share_one_stage_ceiling(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite", stage_limits={"measured": 1})
    tokens = [ledger.create_trial(arm, "measured", "1") for arm in ("mini", "engine")]

    def reserve(i):
        try:
            return ledger.reserve(tokens[i % 2], micros(".3"))[0]
        except BudgetRefused:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = list(pool.map(reserve, range(8)))
    assert len([call for call in admitted if call]) == 3
    assert ledger.snapshot()["spent_or_reserved_usd"] == .9
