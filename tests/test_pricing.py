from copy import deepcopy

import pytest

from codeloom_benchmark import pricing


@pytest.mark.parametrize(
    "prompt,completion,overrides,expected",
    [
        ("0.00000132", "0.00000396", [], "passed"),
        ("0.00000020196", "0.00000060588", [], "passed"),
        ("0.00000132000001", "0.00000396", [], "failed"),
        ("0.00000132", "0.00000396000001", [], "failed"),
        (
            "0.00000132",
            "0.00000396",
            [{"prompt": "0.00000132", "completion": "0.00000396"}],
            "passed",
        ),
        (
            "0.00000132",
            "0.00000396",
            [{"prompt": "0.00000132", "completion": "0.00000396000001"}],
            "failed",
        ),
    ],
)
def test_exact_admission_rates(
    tmp_path, monkeypatch, prompt, completion, overrides, expected
):
    settings = deepcopy(pricing.config())
    endpoint = {
        "provider_name": settings["provider_name"],
        "tag": settings["provider"],
        "pricing": {"prompt": prompt, "completion": completion, "overrides": overrides},
        "supported_parameters": ["reasoning", "tools", "max_tokens"],
        "max_completion_tokens": settings["max_tokens"],
    }

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"endpoints": [endpoint]}}

    monkeypatch.setattr(pricing.requests, "get", lambda *args, **kwargs: Response())
    result = pricing.check(tmp_path / "pricing.json")
    assert result["status"] == expected
    assert result["pricing"] == endpoint["pricing"]
    assert pricing.config() == settings


@pytest.mark.parametrize(
    "field,value",
    [("supported_parameters", ["tools", "max_tokens"]), ("max_completion_tokens", 1)],
)
def test_rates_at_ceiling_still_require_native_capabilities(
    tmp_path, monkeypatch, field, value
):
    settings = pricing.config()
    endpoint = {
        "provider_name": settings["provider_name"],
        "tag": settings["provider"],
        "pricing": {"prompt": "0.00000132", "completion": "0.00000396"},
        "supported_parameters": ["reasoning", "tools", "max_tokens"],
        "max_completion_tokens": settings["max_tokens"],
        field: value,
    }

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": {"endpoints": [endpoint]}}

    monkeypatch.setattr(pricing.requests, "get", lambda *args, **kwargs: Response())
    assert pricing.check(tmp_path / "pricing.json")["status"] == "failed"
