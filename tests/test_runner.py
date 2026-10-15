import json
from types import SimpleNamespace

from codeloom_benchmark.ledger import Ledger
from codeloom_benchmark.manifests import config, tasks
from codeloom_benchmark.runner import attempt, disclosed_prompt


def test_disclosed_prompt_states_allowed_paths_and_task_id():
    bug_task = next(t for t in tasks() if t["id"] == "tinydb-X")
    prompt = disclosed_prompt(bug_task)
    assert prompt.startswith(bug_task["prompt"])
    for path in bug_task["allowed_paths"]:
        assert path in prompt

    nav_task = next(t for t in tasks() if t["id"] == "chi-N")
    nav_prompt = disclosed_prompt(nav_task)
    assert f'"task_id" to exactly "{nav_task["id"]}"' in nav_prompt


def test_budget_pending_does_not_override_completed_termination(tmp_path, monkeypatch):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    broker = SimpleNamespace(
        ledger=ledger,
        settings=config(),
        server=SimpleNamespace(server_address=("localhost", 1)),
    )
    network = SimpleNamespace(name="unused", url="http://unused/v1")
    trial_id = "recovered"
    result_path = tmp_path / "trials" / trial_id / "worker-result.json"

    class Submitted:
        pid = 123

        def communicate(self, *args, **kwargs):
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({"exit_status": "Submitted"}))

        def wait(self):
            pass

        def poll(self):
            return 0

    monkeypatch.setattr(
        "codeloom_benchmark.runner.subprocess.Popen", lambda *a, **k: Submitted()
    )
    # One same-trial admission race mid-run that the agent recovered from on
    # its own -- must not stomp the controller's own "completed" outcome.
    monkeypatch.setattr(
        "codeloom_benchmark.runner.journal",
        lambda *a: [
            {"status": "budget_pending", "detail": "Waiting for this trial's in-flight bill"},
            {"status": "complete"},
        ],
    )
    task = next(t for t in tasks() if t["id"] == "tinydb-B")
    result = attempt(
        task,
        "mini-swe-agent",
        "unused",
        broker,
        network,
        tmp_path,
        trial_id,
        "development",
        60,
        0.5,
    )
    assert result["termination"] == "completed"
    assert result["controller_termination"] == "completed"
    assert result["admission_events"] == ["budget_pending"]


def test_admission_events_collects_every_distinct_non_complete_status(
    tmp_path, monkeypatch
):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    broker = SimpleNamespace(
        ledger=ledger,
        settings=config(),
        server=SimpleNamespace(server_address=("localhost", 1)),
    )
    network = SimpleNamespace(name="unused", url="http://unused/v1")
    trial_id = "mixed"
    result_path = tmp_path / "trials" / trial_id / "worker-result.json"

    class Submitted:
        pid = 123

        def communicate(self, *args, **kwargs):
            result_path.parent.mkdir(parents=True, exist_ok=True)
            result_path.write_text(json.dumps({"exit_status": "Submitted"}))

        def wait(self):
            pass

        def poll(self):
            return 0

    monkeypatch.setattr(
        "codeloom_benchmark.runner.subprocess.Popen", lambda *a, **k: Submitted()
    )
    monkeypatch.setattr(
        "codeloom_benchmark.runner.journal",
        lambda *a: [
            {"status": "budget_pending"},
            {"status": "complete"},
            {"status": "provider_error"},
            {"status": "provider_error"},  # duplicate, must collapse
        ],
    )
    task = next(t for t in tasks() if t["id"] == "tinydb-B")
    result = attempt(
        task,
        "mini-swe-agent",
        "unused",
        broker,
        network,
        tmp_path,
        trial_id,
        "development",
        60,
        0.5,
    )
    assert result["admission_events"] == ["budget_pending", "provider_error"]
