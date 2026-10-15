import hashlib
import json

import pytest

from codeloom_benchmark import cli
from codeloom_benchmark.artifacts import canonical


@pytest.mark.parametrize("image_present", [True, False])
def test_certified_cache_does_not_rebuild_images_unless_missing(
    tmp_path, monkeypatch, image_present
):
    task = {"id": "fixture", "language": "python"}
    cache = tmp_path / ".cache/certification/fixture/certification.json"
    cache.parent.mkdir(parents=True)
    cache.write_text(
        json.dumps(
            {
                "id": "fixture",
                "status": "certified",
                "grading_fingerprint": "contract",
                "suite_fingerprint": "old-suite",
                "image": "cached-image",
            }
        )
    )
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "validate_suite", lambda: None)
    monkeypatch.setattr(cli, "tasks", lambda: [task])
    monkeypatch.setattr(cli, "prepare_runtime", lambda _: {"image": "runtime"})
    monkeypatch.setattr(cli, "fingerprint", lambda: ("current-suite", {}))
    monkeypatch.setattr(cli, "grading_fingerprint", lambda _: "contract")
    actions = []

    def inspect(*args, **kwargs):
        if not image_present:
            raise RuntimeError("Image is missing")
        return json.dumps(
            {
                "org.codeloom.runtime-image": "runtime",
                "org.codeloom.task-fingerprint": hashlib.sha256(
                    canonical(task)
                ).hexdigest(),
            }
        )

    def build(_):
        actions.append("build")
        return "rebuilt-image"

    def certify(_, image):
        actions.append("certify")
        return {"id": "fixture", "status": "certified", "image": image}

    monkeypatch.setattr(cli, "command", inspect)
    monkeypatch.setattr(cli, "task_image", build)
    monkeypatch.setattr(cli, "certify", certify)
    monkeypatch.setattr(cli, "audit_image", lambda image, task: {"status": "pass"})
    result = cli.certification(tmp_path / "engine")
    assert result["status"] == "certified"
    assert actions == ([] if image_present else ["build", "certify"])
    if image_present:
        assert result["tasks"][0]["suite_fingerprint"] == "current-suite"
        assert result["tasks"][0]["cache_reused"] is True
