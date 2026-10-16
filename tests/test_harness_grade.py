import json

from harness.grade import grade_navigation, stable_kill
from harness.tasks import load_suite


def test_navigation_accepts_chi_n_reference(tmp_path):
    task = next(row for row in load_suite("classic") if row["id"] == "chi-N")
    answer = json.loads(task["reference"]["navigation-answer.json"])
    (tmp_path / "navigation-answer.json").write_text(json.dumps(answer))
    result = grade_navigation(task, tmp_path)
    assert result["pass"]
    assert result["missing_symbols"] == []
    assert result["missing_edges"] == []
    assert result["missing_claims"] == []


def test_navigation_rejects_wrong_claims(tmp_path):
    task = next(row for row in load_suite("classic") if row["id"] == "chi-N")
    answer = json.loads(task["reference"]["navigation-answer.json"])
    answer["claims"]["pool"] = "one context shared by all requests"
    (tmp_path / "navigation-answer.json").write_text(json.dumps(answer))
    result = grade_navigation(task, tmp_path)
    assert not result["pass"]
    assert "pool" in result["missing_claims"]


def test_stable_kill_requires_matching_assertion_failures():
    ok = {
        "returncode": 1,
        "assertion_signature": (("test_foo", 12),),
    }
    assert stable_kill([ok, ok, ok])
    assert not stable_kill([ok, ok, {**ok, "returncode": 0}])
    mixed = [ok, ok, {**ok, "assertion_signature": (("test_bar", 1),)}]
    assert not stable_kill(mixed)
