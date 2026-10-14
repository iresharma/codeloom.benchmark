import pytest
import requests

from codeloom_benchmark.broker import Broker
from codeloom_benchmark.ledger import Ledger
from codeloom_benchmark import campaign, manifests
from codeloom_benchmark.cost_analysis import estimate

SETTINGS = {
    "model": "test/model",
    "provider": "test",
    "reasoning_effort": "high",
    "thinking_enabled": True,
    "max_tokens": 100,
    "input_per_million": 1.32,
    "output_per_million": 3.96,
}


@pytest.mark.parametrize(
    "native", [{"reasoning_effort": "high"}, {"reasoning": {"effort": "high"}}]
)
def test_native_controls_normalize_only_after_matching(tmp_path, native):
    broker = Broker(Ledger(tmp_path / "ledger"), tmp_path, "fake", SETTINGS)
    history = [
        {
            "role": "assistant",
            "content": "",
            "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque"}],
        }
    ]
    wire, reserved = broker.prepare(
        {"model": "test/model", "messages": history, **native}
    )
    assert wire["reasoning"] == {"effort": "high", "enabled": True, "exclude": False}
    assert wire["messages"] == history and reserved > 0


@pytest.mark.parametrize(
    "native",
    [
        {},
        {"reasoning_effort": "low"},
        {"reasoning_effort": "high", "reasoning": {"effort": "max"}},
        {"reasoning": {"effort": "high", "exclude": True}},
        {"reasoning": {"effort": "high", "enabled": False}},
        {"reasoning": {"effort": "high", "max_tokens": 500}},
        {"reasoning": {"effort": "high", "mode": "pro"}},
    ],
)
def test_broker_rejects_missing_or_conflicting_native_controls(tmp_path, native):
    broker = Broker(Ledger(tmp_path / "ledger"), tmp_path, "fake", SETTINGS)
    with pytest.raises(ValueError):
        broker.prepare(
            {
                "model": "test/model",
                "messages": [{"role": "user", "content": "task"}],
                **native,
            }
        )


def test_wrong_model_cannot_be_silently_fixed(tmp_path):
    broker = Broker(Ledger(tmp_path / "ledger"), tmp_path, "fake", SETTINGS)
    with pytest.raises(ValueError, match="model"):
        broker.prepare(
            {
                "model": "wrong/model",
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": "task"}],
            }
        )


SETTINGS_WITH_SECONDARY = {
    **SETTINGS,
    "secondary_models": {
        "test/cheap-model": {
            "provider": "cheap-provider",
            "provider_name": "CheapProvider",
            "input_per_million": 1.0,
            "output_per_million": 5.0,
        }
    },
}


def test_approved_secondary_model_is_admitted_with_its_own_pricing(tmp_path):
    broker = Broker(Ledger(tmp_path / "ledger"), tmp_path, "fake", SETTINGS_WITH_SECONDARY)
    wire, reserved = broker.prepare(
        {
            "model": "test/cheap-model",
            "reasoning_effort": "high",
            "messages": [{"role": "user", "content": "write a test"}],
        }
    )
    assert wire["model"] == "test/cheap-model"
    assert wire["provider"]["only"] == ["cheap-provider"]
    assert wire["provider"]["max_price"] == {"prompt": 1.0, "completion": 5.0}
    assert reserved > 0


def test_unlisted_secondary_model_is_still_rejected(tmp_path):
    broker = Broker(Ledger(tmp_path / "ledger"), tmp_path, "fake", SETTINGS_WITH_SECONDARY)
    with pytest.raises(ValueError, match="model"):
        broker.prepare(
            {
                "model": "test/unapproved-model",
                "reasoning_effort": "high",
                "messages": [{"role": "user", "content": "task"}],
            }
        )


def test_pinned_catalog_is_local_authenticated_and_revocable(tmp_path):
    ledger = Ledger(tmp_path / "ledger")
    token = ledger.create_trial("trial", "development", 1)
    broker = Broker(
        ledger,
        tmp_path,
        "fake",
        SETTINGS,
        upstream="http://127.0.0.1:1",
        model_metadata={"id": "test/model"},
    )
    port = broker.start()
    try:
        url = f"http://127.0.0.1:{port}/v1/models?supported_parameters=reasoning"
        assert requests.get(url, timeout=3).status_code == 401
        r = requests.get(url, headers={"Authorization": "Bearer " + token}, timeout=3)
        assert r.json() == {"data": [{"id": "test/model"}]}
        ledger.revoke("trial")
        assert (
            requests.get(
                url, headers={"Authorization": "Bearer " + token}, timeout=3
            ).status_code
            == 401
        )
        with ledger.connection() as db:
            assert db.execute("SELECT COUNT(*) FROM calls").fetchone()[0] == 0
    finally:
        broker.close()


def test_paid_hold_stops_before_credentials_or_network(tmp_path, monkeypatch):
    settings = {**campaign.config(), "paid_runs_authorized": False}
    monkeypatch.setattr(campaign, "config", lambda: settings)
    monkeypatch.setattr(
        campaign, "credentials", lambda: pytest.fail("Read real key during hold")
    )
    monkeypatch.setattr(
        "codeloom_benchmark.pricing.check",
        lambda: pytest.fail("Price network during hold"),
    )
    monkeypatch.setattr(
        "codeloom_benchmark.compatibility.check",
        lambda *args: pytest.fail("Unneeded CPU work during hold"),
    )
    with pytest.raises(ValueError, match="on hold"):
        campaign.services(tmp_path)
    with pytest.raises(ValueError, match="on hold"):
        campaign.calibrate(tmp_path, tmp_path / "engine")


def test_model_rejects_generic_efforts_it_does_not_advertise(monkeypatch):
    settings = manifests.config()
    assert manifests.model_metadata()["reasoning"]["supported_efforts"] == [
        "max",
        "high",
        "low",
    ]
    monkeypatch.setattr(
        manifests, "config", lambda: dict(settings, reasoning_effort="minimal")
    )
    with pytest.raises(ValueError, match="Unsupported"):
        manifests.model_metadata()


def test_reasoning_is_billed_once_with_visible_output():
    assert estimate(300_000, 30_000, 10_000, 1.32, 3.96) == pytest.approx(0.5544)
    assert estimate(100_000, 8_000, 2_000, 1.32, 3.96) == pytest.approx(0.1716)


def test_cost_report_weights_selected_task_cohorts_and_exposes_oversubscription(
    tmp_path,
):
    from codeloom_benchmark.cost_analysis import analyze

    report = analyze(destination=tmp_path / "cost.json", refresh=False)
    assert report["planned_attempts"] == 20
    assert report["weighted_reservation_price_uncapped_total"] == pytest.approx(
        26.32608
    )
    assert report["primary_attempt_maxima_sum"] == 50
    assert report["budget"]["measurement"] == 32
    assert report["budget"]["total_ceiling"] == 50
    assert report["stress"]["fits_heavy_cap"] is False
