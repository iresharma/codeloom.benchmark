import json
from codeloom_benchmark.fixtures import snapshot
from codeloom_benchmark.graders.navigation import grade
from codeloom_benchmark.manifests import tasks


def test_navigation_requires_correct_claims_and_evidence(tmp_path):
    task = next(t for t in tasks() if t["id"] == "chi-N")
    reference = snapshot(task, tmp_path / "reference", reference=True)
    assert grade(task, reference)["pass"]
    p = reference / "navigation-answer.json"
    answer = json.loads(p.read_text())
    answer["claims"]["precedence"] = "opposite behavior"
    p.write_text(json.dumps(answer))
    assert not grade(task, reference)["pass"]


def test_all_navigation_rubrics_accept_qualified_equivalents(tmp_path):
    for task in tasks():
        if task["category"] != "navigation":
            continue
        source = snapshot(task, tmp_path / task["id"], reference=True)
        path = source / "navigation-answer.json"
        answer = json.loads(path.read_text())
        for symbol in answer["symbols"]:
            symbol["name"] = symbol["name"].replace(".", "::") + "()"
        for edge in answer["edges"]:
            edge["from"] = edge["from"].replace(".", "::") + "()"
            edge["to"] = edge["to"].replace(".", "::") + "()"
        path.write_text(json.dumps(answer))
        assert grade(task, source)["pass"], task["id"]
        answer["edges"][0]["to"] = "nonexistent_call_target"
        path.write_text(json.dumps(answer))
        assert not grade(task, source)["pass"], task["id"]
