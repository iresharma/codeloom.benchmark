import os
import subprocess
import sys
import tempfile


def _write(tmpdir, name, content):
    path = os.path.join(tmpdir, name)
    with open(path, "w") as f:
        f.write(content)
    return path


def _run_diff(*args):
    return subprocess.run(
        [sys.executable, "-m", "dotenv", "diff", *args],
        capture_output=True,
        text=True,
    )


def test_identical_files_exit_zero_no_output():
    with tempfile.TemporaryDirectory() as d:
        f1 = _write(d, "f1.env", "A=1\nB=2\n")
        f2 = _write(d, "f2.env", "A=1\nB=2\n")
        result = _run_diff(f1, f2)
        assert result.returncode == 0
        assert result.stdout.strip() == ""


def test_added_removed_changed_keys_reported_and_exit_one():
    with tempfile.TemporaryDirectory() as d:
        f1 = _write(d, "f1.env", "A=1\nB=2\nC=3\n")
        f2 = _write(d, "f2.env", "A=1\nB=20\nD=4\n")
        result = _run_diff(f1, f2)
        assert result.returncode == 1

        lines = result.stdout.strip().splitlines()
        added = [line for line in lines if line.startswith("+")]
        removed = [line for line in lines if line.startswith("-")]
        changed = [line for line in lines if line.startswith("~")]

        assert len(added) == 1 and "D" in added[0] and "4" in added[0]
        assert len(removed) == 1 and "C" in removed[0] and "3" in removed[0]
        assert len(changed) == 1
        assert "B" in changed[0] and "2" in changed[0] and "20" in changed[0]
        # A is unchanged and must not be reported at all.
        assert not any("A" in line for line in lines)


def test_output_sorted_by_key_name():
    with tempfile.TemporaryDirectory() as d:
        # Keys deliberately out of alphabetical order in the files, all of
        # them changed, to check the report is sorted by key, not by file
        # order.
        f1 = _write(d, "f1.env", "ZETA=1\nALPHA=1\nMID=1\n")
        f2 = _write(d, "f2.env", "ZETA=2\nALPHA=2\nMID=2\n")
        result = _run_diff(f1, f2)
        assert result.returncode == 1
        lines = [line for line in result.stdout.strip().splitlines() if line.startswith("~")]
        keys_in_order = [line.split()[1].rstrip(":") for line in lines]
        assert keys_in_order == sorted(keys_in_order) == ["ALPHA", "MID", "ZETA"]


def test_only_additions():
    with tempfile.TemporaryDirectory() as d:
        f1 = _write(d, "f1.env", "A=1\n")
        f2 = _write(d, "f2.env", "A=1\nB=2\n")
        result = _run_diff(f1, f2)
        assert result.returncode == 1
        assert "+ B=2" in result.stdout

        # Direction matters: diffing the other way should report a removal.
        result_rev = _run_diff(f2, f1)
        assert result_rev.returncode == 1
        assert "- B=2" in result_rev.stdout
