import json
from pathlib import Path
from unittest.mock import Mock

from codeloom_benchmark import cli


def test_runtime_only_command_does_not_certify_or_start_paid_calls(monkeypatch, capsys):
    prepare = Mock(return_value={"image": "new-runtime", "engine_commit": "new-engine"})
    monkeypatch.setattr(cli, "prepare_runtime", prepare)
    forbidden = Mock(side_effect=AssertionError("runtime preparation must only build"))
    monkeypatch.setattr(cli, "certification", forbidden)
    monkeypatch.setattr(cli, "check", forbidden)
    from codeloom_benchmark import campaign

    monkeypatch.setattr(campaign, "calibrate", forbidden)
    monkeypatch.setattr(
        cli.sys, "argv",
        ["benchmark", "prepare-runtime", "--engine", "/checkout/engine"],
    )
    cli.main()
    prepare.assert_called_once_with(Path("/checkout/engine"))
    forbidden.assert_not_called()
    summary = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert summary["status"] == "ready"
