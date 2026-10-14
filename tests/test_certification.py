import json
import shutil
from codeloom_benchmark.certification import GRADE_FILES, grading_fingerprint
from codeloom_benchmark.manifests import ROOT, tasks


def test_certification_cache_invalidates_own_oracle_recipe_and_runtime(tmp_path):
    task = next(t for t in tasks() if t["id"] == "chi-N")
    for rel in GRADE_FILES:
        destination = tmp_path / rel
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, destination)
    shutil.copytree(
        ROOT / "codeloom_benchmark/graders", tmp_path / "codeloom_benchmark/graders"
    )
    own = tmp_path / "evaluator/v1/chi-N/rubric.json"
    own.parent.mkdir(parents=True)
    own.write_text("original oracle")
    runtime = tmp_path / ".cache/runtime.json"
    runtime.parent.mkdir()
    runtime.write_text(json.dumps({"image": "runtime-one"}))
    first = grading_fingerprint(task, tmp_path)
    other = tmp_path / "evaluator/v1/other/rubric.json"
    other.parent.mkdir(parents=True)
    other.write_text("unrelated task")
    assert grading_fingerprint(task, tmp_path) == first
    own.write_text("changed oracle")
    assert grading_fingerprint(task, tmp_path) != first
    own.write_text("original oracle")
    assert grading_fingerprint(dict(task, reference={}), tmp_path) != first
    runtime.write_text(json.dumps({"image": "runtime-two"}))
    assert grading_fingerprint(task, tmp_path) != first
