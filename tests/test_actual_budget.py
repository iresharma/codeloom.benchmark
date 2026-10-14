from concurrent.futures import ThreadPoolExecutor
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from codeloom_benchmark.broker import Broker
from codeloom_benchmark.ledger import BudgetRefused, Ledger, micros


def account(tmp_path, **kwargs):
    return Ledger(tmp_path / "ledger.sqlite", budget_policy="actual-spend", **kwargs)


def test_estimate_can_exceed_remaining_budget_but_actual_charge_stops_next_call(
    tmp_path,
):
    ledger = account(tmp_path)
    token = ledger.create_trial("engine", "measured", 1)
    # $1 estimate, $0.20 bill must not end the task.
    for _ in range(4):
        call, _ = ledger.reserve(token, micros(1))
        ledger.settle(call, ".2")
    # At $0.80 actual spend, a $3 estimate is allowed (inclusive 3x threshold).
    call, _ = ledger.reserve(token, micros(3))
    ledger.settle(call, ".3")
    assert ledger.snapshot()["actual_spent_usd"] == 1.1
    assert not ledger.snapshot()["overrun"]
    with pytest.raises(BudgetRefused, match="Actual spending target reached"):
        ledger.reserve(token, micros(".01"))


def test_only_estimates_strictly_above_three_times_full_trial_budget_are_rejected(
    tmp_path,
):
    ledger = account(tmp_path)
    token = ledger.create_trial("engine", "measured", ".5")
    with pytest.raises(BudgetRefused) as refused:
        ledger.reserve(token, micros("1.500001"))
    assert refused.value.reason == "call_cost_limit"
    assert not ledger.pending_can_release_budget(token, micros("1.500001"))
    assert ledger.snapshot()["actual_spent_usd"] == 0
    call, _ = ledger.reserve(token, micros("1.5"))
    ledger.settle(call, ".49")
    # The threshold uses the full $0.50 budget, not the $0.01 remaining.
    call, _ = ledger.reserve(token, micros("1.5"))
    ledger.settle(call, ".02")
    with pytest.raises(BudgetRefused):
        ledger.reserve(token, 1)


def test_parallel_children_wait_for_bill_but_other_arm_can_run(tmp_path):
    ledger = account(tmp_path)
    a = ledger.create_trial("engine", "measured", 1)
    b = ledger.create_trial("mini", "measured", 1)

    def admit(_):
        try:
            return ledger.reserve(a, micros(2))[0]
        except BudgetRefused as exc:
            assert exc.reason == "budget_pending"
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted = [call for call in pool.map(admit, range(8)) if call]
    assert len(admitted) == 1
    assert ledger.pending_can_release_budget(a, micros(2))
    assert not ledger.pending_can_release_budget(b, micros(2))
    other, _ = ledger.reserve(b, micros(2))
    ledger.settle(admitted[0], 1)
    assert not ledger.pending_can_release_budget(a, micros(2))
    with pytest.raises(BudgetRefused, match="Actual spending target"):
        ledger.reserve(a, micros(2))
    ledger.settle(other, ".1")


@pytest.mark.parametrize(
    "campaign,stages", [(1, {"measured": 10}), (10, {"measured": 1})]
)
def test_campaign_and_stage_targets_use_actual_charges_not_estimates(
    tmp_path, campaign, stages
):
    ledger = account(tmp_path, campaign_limit=campaign, stage_limits=stages)
    a = ledger.create_trial("a", "measured", 2)
    b = ledger.create_trial("b", "measured", 2)
    call, _ = ledger.reserve(a, micros(3))
    ledger.settle(call, ".9")
    call, _ = ledger.reserve(b, micros(3))
    ledger.settle(call, ".2")
    assert ledger.headroom("measured") == pytest.approx(-0.1)
    with pytest.raises(BudgetRefused, match="Actual spending target"):
        ledger.reserve(a, micros(".1"))


def test_unknown_bill_still_halts_actual_spend_policy(tmp_path):
    ledger = account(tmp_path)
    a = ledger.create_trial("a", "measured", 1)
    b = ledger.create_trial("b", "measured", 1)
    call, _ = ledger.reserve(a, micros(3))
    ledger.settle(call, None, "transport_error")
    with pytest.raises(BudgetRefused, match="unknown bills"):
        ledger.reserve(b, micros(".1"))
    assert not ledger.pending_can_release_budget(a, micros(".1"))
    assert ledger.snapshot()["spent_or_reserved_usd"] == 3


def test_migration_preserves_old_charges_and_freezes_new_policy(tmp_path):
    path = tmp_path / "ledger.sqlite"
    ledger = Ledger(path)
    token = ledger.create_trial("old-smoke", "development", ".5")
    call, _ = ledger.reserve(token, micros(".4"))
    with pytest.raises(ValueError, match="settled development-only"):
        ledger.migrate_to_actual_spend()
    ledger.settle(call, ".02")
    ledger.revoke("old-smoke")
    with pytest.raises(ValueError, match="explicitly migrate"):
        Ledger(path, budget_policy="actual-spend")
    ledger.migrate_to_actual_spend()
    reopened = Ledger(path)
    assert reopened.snapshot()["budget_policy"] == "actual-spend"
    assert reopened.snapshot()["actual_spent_usd"] == 0.02
    with pytest.raises(ValueError, match="cannot change"):
        Ledger(path, budget_policy="actual-spend", call_limit_multiplier=4)
    with pytest.raises(ValueError, match="cannot change"):
        reopened.migrate_to_actual_spend(4)


def test_measured_history_cannot_be_reinterpreted_by_policy_migration(tmp_path):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    ledger.create_trial("measured", "measured", 1)
    ledger.revoke("measured")
    with pytest.raises(ValueError, match="settled development-only"):
        ledger.migrate_to_actual_spend()


def test_http_broker_allows_overshoot_then_refuses_without_paid_request_and_logs_estimate(
    tmp_path,
):
    seen = []

    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            seen.append(
                json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            )
            body = json.dumps(
                {
                    "model": "test/model",
                    "provider": "test",
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "usage": {"cost": 0.001, "completion_tokens": 4},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    ledger = account(tmp_path)
    token = ledger.create_trial("trial", "measured", ".0015")
    cap_token = ledger.create_trial("cap", "measured", ".0015")
    settings = {
        "model": "test/model",
        "provider": "test/fp8",
        "max_tokens": 100,
        "reasoning_effort": "high",
        "input_per_million": 0.3,
        "output_per_million": 0.9,
    }
    broker = Broker(
        ledger,
        tmp_path,
        "private-key",
        settings,
        f"http://127.0.0.1:{upstream.server_port}",
    )
    port = broker.start()

    def post(key, content="hello"):
        return requests.post(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            headers={"Authorization": "Bearer " + key},
            json={
                "model": "test/model",
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": content}],
            },
            timeout=5,
        )

    try:
        assert post(token).status_code == 200
        assert post(token).status_code == 200
        assert ledger.snapshot()["actual_spent_usd"] == 0.002
        stopped = post(token)
        assert stopped.status_code == 402
        assert "Actual spending target reached" in stopped.text
        assert post(cap_token, "x" * 50000).status_code == 402
        assert len(seen) == 2
        journal = json.loads((tmp_path / "trials/cap/broker.jsonl").read_text())
        assert journal["status"] == "call_cost_limit"
        assert journal["estimated_cost_usd"] > 0.0045
        assert (tmp_path / journal["request_blob"]).exists()
        assert "private-key" not in json.dumps(journal)
    finally:
        broker.close()
        upstream.shutdown()
        upstream.server_close()
