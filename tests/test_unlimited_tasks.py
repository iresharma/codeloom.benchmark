import json
from types import SimpleNamespace

import pytest

from codeloom_benchmark.ledger import Ledger
from codeloom_benchmark.manifests import config, tasks
from codeloom_benchmark.runner import attempt
from codeloom_benchmark.worker import container_lifetime
from minisweagent.environments.docker import DockerEnvironment


def test_native_mini_container_has_no_expiration_but_remains_owned_by_parent(
    monkeypatch,
):
    commands = []

    def run(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(stdout="container-id\n")

    monkeypatch.setattr("minisweagent.environments.docker.subprocess.run", run)
    env = DockerEnvironment(image="fixture", container_timeout=container_lifetime(None))
    assert commands[0][-3:] == ["fixture", "sleep", "infinity"]
    assert env.container_id == "container-id"
    env.container_id = None  # the runner owns sealing/removal


@pytest.mark.parametrize("interrupted", [False, True])
def test_unlimited_controller_waits_without_deadline_and_can_still_be_cancelled(
    tmp_path, monkeypatch, interrupted
):
    ledger = Ledger(tmp_path / "ledger.sqlite")
    broker = SimpleNamespace(
        ledger=ledger,
        settings=config(),
        server=SimpleNamespace(server_address=("localhost", 1)),
    )
    network = SimpleNamespace(name="unused", url="http://unused/v1")
    killed = []
    trial = tmp_path / "trials/unlimited"

    class Controller:
        pid = 123
        alive = True

        def communicate(self, payload, *, timeout):
            assert timeout is None
            assert json.loads(payload)["watchdog_s"] is None
            if interrupted:
                raise KeyboardInterrupt()
            self.alive = False
            (trial / "worker-result.json").write_text(
                json.dumps({"exit_status": "Submitted"})
            )

        def poll(self):
            return None if self.alive else 0

        def wait(self):
            self.alive = False

    monkeypatch.setattr(
        "codeloom_benchmark.runner.subprocess.Popen", lambda *a, **k: Controller()
    )
    monkeypatch.setattr(
        "codeloom_benchmark.runner.os.killpg", lambda pid, sig: killed.append(pid)
    )
    task = next(t for t in tasks() if t["id"] == "tinydb-B")
    args = (
        task,
        "mini-swe-agent",
        "unused",
        broker,
        network,
        tmp_path,
        "unlimited",
        "development",
        None,
        0.5,
    )
    if interrupted:
        with pytest.raises(KeyboardInterrupt):
            attempt(*args)
        assert killed == [123]
    else:
        result = attempt(*args)
        assert result["termination"] == "completed"
        assert result["watchdog_s"] is None
        assert killed == []
    assert not ledger.active_trials()
