import json
from types import SimpleNamespace

import pytest
from codeloom_benchmark import campaign


@pytest.mark.parametrize(
    "termination, expected", [("completed", "passed"), ("budget_exhausted", "failed")]
)
def test_calibration_uses_broker_throughput_and_requires_completion(
    tmp_path, monkeypatch, termination, expected
):
    monkeypatch.setattr(campaign, "ROOT", tmp_path)
    current = campaign.config()
    monkeypatch.setattr(
        campaign, "config", lambda: dict(current, paid_runs_authorized=True)
    )
    (tmp_path / ".cache").mkdir()
    (tmp_path / ".cache/runtime.json").write_text(
        json.dumps({"image": "frozen-runtime"})
    )
    monkeypatch.setattr(
        "codeloom_benchmark.compatibility.check", lambda *args: {"status": "passed"}
    )
    monkeypatch.setattr(
        campaign,
        "tasks",
        lambda *args: [{"id": "dev-python"}, {"id": "dev-typescript"}],
    )
    monkeypatch.setattr(
        "codeloom_benchmark.sandbox.task_image", lambda *args: "task-image"
    )
    monkeypatch.setattr("codeloom_benchmark.sandbox.audit_image", lambda *args: None)
    monkeypatch.setattr(
        "codeloom_benchmark.evaluate.certify", lambda *args: {"status": "certified"}
    )
    closed = []
    monkeypatch.setattr(
        campaign,
        "services",
        lambda *args: (
            SimpleNamespace(close=lambda: closed.append("broker")),
            SimpleNamespace(close=lambda: closed.append("network")),
        ),
    )

    def attempt(task, system, image, broker, network, root, trial_id, *args):
        trial = root / "trials" / trial_id
        trial.mkdir(parents=True)
        (trial / "broker.jsonl").write_text(
            json.dumps(
                {
                    "status": "complete",
                    "generated_tokens_per_elapsed_s": 40,
                    "first_byte_s": 2,
                }
            )
            + "\n"
        )
        return {
            "id": trial_id,
            "termination": termination,
            "verification": {"pass": True},
            "cost_is_conservative": False,
            "elapsed_s": 800,
        }

    monkeypatch.setattr(campaign, "attempt", attempt)
    result = campaign.calibrate(tmp_path / "calibration", tmp_path / "engine")
    assert result["status"] == expected
    assert len(result["attempts"]) == 4
    assert result["watchdog_s"] == 3600
    assert result["request_timeout_s"] == pytest.approx(2 * (32768 / 40 + 2) + 120)
    assert result["runtime_image"] == "frozen-runtime"
    assert len(result["observed_generated_tokens_per_elapsed_s"]) == 4
    assert closed == ["network", "broker"]
