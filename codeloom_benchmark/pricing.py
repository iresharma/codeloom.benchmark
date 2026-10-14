"""Read-only endpoint preflight. Never substitutes a provider or raises budget limits."""

from datetime import datetime, timezone
import requests
from .artifacts import write_json
from .manifests import ROOT, config


def check(destination=None):
    settings = config()
    url = "https://openrouter.ai/api/v1/models/" + settings["model"] + "/endpoints"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    endpoint = next(
        (
            e
            for e in response.json()["data"]["endpoints"]
            if e.get("tag") == settings["provider"]
        ),
        None,
    )
    if not endpoint:
        result = {
            "status": "failed",
            "reason": "Pinned endpoint is unavailable",
            "url": url,
        }
    else:
        price = endpoint["pricing"]
        # Conservatively check every advertised time/day override too; no unnoticed schedule price jump.
        variants = [price, *price.get("overrides", [])]
        parameters_ok = {"reasoning", "tools", "max_tokens"}.issubset(
            endpoint.get("supported_parameters", [])
        )
        completion_ok = (
            endpoint.get("max_completion_tokens") is None
            or endpoint["max_completion_tokens"] >= settings["max_tokens"]
        )
        admitted = (
            parameters_ok
            and completion_ok
            and all(
                float(p["prompt"]) * 1_000_000 <= settings["input_per_million"]
                and float(p["completion"]) * 1_000_000 <= settings["output_per_million"]
                for p in variants
            )
        )
        result = {
            "status": "passed" if admitted else "failed",
            "provider": endpoint["provider_name"],
            "tag": endpoint["tag"],
            "pricing": price,
            "supported_parameters": endpoint.get("supported_parameters", []),
            "max_completion_tokens": endpoint.get("max_completion_tokens"),
            "reasoning_effort": settings["reasoning_effort"],
            "quantization": endpoint.get("quantization"),
            "url": url,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": "Pinned endpoint is within reservation rates."
            if admitted
            else "Pinned endpoint exceeds configured admission rates. No provider fallback or budget increase is permitted.",
        }
    write_json(destination or ROOT / "reports/pricing.json", result)
    return result
