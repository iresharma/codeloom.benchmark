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


def test_override_files_apply_in_order_last_wins():
    with tempfile.TemporaryDirectory() as d:
        base = _write(d, "base.env", "A=base\nB=base\nC=base\n")
        o1 = _write(d, "o1.env", "A=o1\nB=o1\n")
        o2 = _write(d, "o2.env", "A=o2\n")

        values = dotenv_values(base, override_files=[o1, o2])
        assert values == {"A": "o2", "B": "o1", "C": "base"}


def test_override_files_order_matters():
    with tempfile.TemporaryDirectory() as d:
        base = _write(d, "base.env", "A=base\n")
        o1 = _write(d, "o1.env", "A=o1\n")
        o2 = _write(d, "o2.env", "A=o2\n")

        forward = dotenv_values(base, override_files=[o1, o2])
        backward = dotenv_values(base, override_files=[o2, o1])
        assert forward["A"] == "o2"
        assert backward["A"] == "o1"


def test_no_override_files_behaves_as_before():
    with tempfile.TemporaryDirectory() as d:
        base = _write(d, "base.env", "A=base\n")
        assert dotenv_values(base) == {"A": "base"}
        assert dotenv_values(base, override_files=[]) == {"A": "base"}
        assert dotenv_values(base, override_files=None) == {"A": "base"}


def test_load_dotenv_applies_override_files_to_process_env(monkeypatch):
    with tempfile.TemporaryDirectory() as d:
        base = _write(d, "base.env", "A=base\n")
        o1 = _write(d, "o1.env", "A=override\n")
        monkeypatch.delenv("A", raising=False)
        load_dotenv(base, override_files=[o1], override=True)
        assert os.environ["A"] == "override"


def test_override_flag_semantics_unaffected_by_override_files(monkeypatch):
    # The pre-existing `override` flag still only governs precedence against
    # the process environment, not the relationship between the base file
    # and override_files.
    with tempfile.TemporaryDirectory() as d:
        base = _write(d, "base.env", "A=base\n")
        o1 = _write(d, "o1.env", "A=fromfile\n")
        monkeypatch.setenv("A", "from_process_env")

        # override=False: process env wins over everything from files.
        load_dotenv(base, override_files=[o1], override=False)
        assert os.environ["A"] == "from_process_env"

        # override=True: file value (from the override file, since it's last)
        # wins over the process env.
        load_dotenv(base, override_files=[o1], override=True)
        assert os.environ["A"] == "fromfile"


def test_cli_override_file_flag_list():
    with tempfile.TemporaryDirectory() as d:
        base = _write(d, "base.env", "A=base\nB=base\n")
        o1 = _write(d, "o1.env", "A=o1\n")
        result = subprocess.run(
            [
                sys.executable, "-m", "dotenv",
                "-f", base,
                "--override-file", o1,
                "list",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        lines = set(result.stdout.strip().splitlines())
        assert lines == {"A=o1", "B=base"}


def test_cli_override_file_flag_get():
    with tempfile.TemporaryDirectory() as d:
        base = _write(d, "base.env", "A=base\n")
        o1 = _write(d, "o1.env", "A=o1\n")
        o2 = _write(d, "o2.env", "A=o2\n")
        result = subprocess.run(
            [
                sys.executable, "-m", "dotenv",
                "-f", base,
                "--override-file", o1,
                "--override-file", o2,
                "get", "A",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == "o2"
