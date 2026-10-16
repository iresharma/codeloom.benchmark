import os
import subprocess
import sys
import tempfile

from dotenv import dotenv_values, load_dotenv


def _write(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w") as f:
        f.write(content)
    return path


def test_default_used_when_unset():
    with tempfile.TemporaryDirectory() as d:
        path = _write(d, ".env", "A=${B:-fallback}\n")
        values = dotenv_values(path)
        assert values == {"A": "fallback"}


def test_default_used_when_empty():
    with tempfile.TemporaryDirectory() as d:
        path = _write(d, ".env", "B=\nA=${B:-fallback}\n")
        values = dotenv_values(path)
        assert values["B"] == ""
        assert values["A"] == "fallback", "empty value must trigger the default"


def test_existing_nonempty_value_wins_over_default():
    with tempfile.TemporaryDirectory() as d:
        path = _write(d, ".env", "B=real\nA=${B:-fallback}\n")
        values = dotenv_values(path)
        assert values == {"B": "real", "A": "real"}


def test_plain_interpolation_still_works_unchanged():
    with tempfile.TemporaryDirectory() as d:
        path = _write(d, ".env", "B=hello\nA=${B}\n")
        values = dotenv_values(path)
        assert values == {"B": "hello", "A": "hello"}

        path2 = _write(d, "unset.env", "A=${MISSING}\n")
        values2 = dotenv_values(path2)
        assert values2 == {"A": ""}


def test_default_may_contain_nested_reference():
    with tempfile.TemporaryDirectory() as d:
        path = _write(d, ".env", "C=deep\nA=${B:-${C}}\n")
        values = dotenv_values(path)
        assert values == {"C": "deep", "A": "deep"}


def test_default_nested_reference_can_itself_have_a_default():
    with tempfile.TemporaryDirectory() as d:
        path = _write(d, ".env", "A=${B:-${C:-tail}}\n")
        values = dotenv_values(path)
        assert values == {"A": "tail"}


def test_load_dotenv_applies_defaults_to_process_env(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        path = _write(d, ".env", "A=${MISSING_ENV_VAR:-loaded}\n")
        monkeypatch.delenv("MISSING_ENV_VAR", raising=False)
        monkeypatch.delenv("A", raising=False)
        load_dotenv(path, override=True)
        assert os.environ["A"] == "loaded"


def test_cli_get_resolves_default():
    with tempfile.TemporaryDirectory() as d:
        path = _write(d, ".env", "A=${MISSING:-cli-default}\n")
        result = subprocess.run(
            [sys.executable, "-m", "dotenv", "-f", path, "get", "A"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "cli-default"


def test_cli_list_resolves_default():
    with tempfile.TemporaryDirectory() as d:
        path = _write(d, ".env", "A=${MISSING:-cli-default}\n")
        result = subprocess.run(
            [sys.executable, "-m", "dotenv", "-f", path, "list"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert "A=cli-default" in result.stdout.strip().splitlines()
