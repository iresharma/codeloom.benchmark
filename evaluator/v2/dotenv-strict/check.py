from io import StringIO
import pytest
from dotenv import load_dotenv, dotenv_values, DotEnvParseError
from click.testing import CliRunner
from dotenv.cli import cli


def test_strict(monkeypatch, tmp_path):
    monkeypatch.delenv("BENCH_STRICT", raising=False)
    with pytest.raises(DotEnvParseError) as exc:
        load_dotenv(stream=StringIO('BENCH_STRICT=yes\nBAD="unclosed'), strict=True)
    assert exc.value.line == 2 and exc.value.path is None
    import os

    assert "BENCH_STRICT" not in os.environ
    assert dotenv_values(stream=StringIO('A=ok\nBAD="unclosed')) == {"A": "ok"}
    assert dotenv_values(stream=StringIO("A=ok"), strict=True) == {"A": "ok"}
    path = tmp_path / "bad.env"
    path.write_text('BAD="unclosed')
    assert (
        CliRunner()
        .invoke(cli, ["-f", str(path), "run", "--strict", "python", "-c", "pass"])
        .exit_code
        != 0
    )
