import json
import sys
from pathlib import Path

from ..manifests import ROOT

sys.path.insert(0, str(ROOT / "public"))
from validate_answer import validate  # noqa: E402


def grade(task: dict, source: Path):
    try:
        answer = json.loads((source / "navigation-answer.json").read_text())
    except (ValueError, OSError) as exc:
        return {"pass": False, "reason": "invalid_answer", "detail": str(exc)}
    errors = validate(answer)
    if errors or answer.get("task_id") != task["id"]:
        return {"pass": False, "reason": "invalid_answer", "errors": errors}
    rubric = json.loads(
        (ROOT / "evaluator/v1" / task["id"] / "rubric.json").read_text()
    )

    def aliases(name):
        values = {
            name,
            name.replace(".#", "."),
            name.replace(".#", "#"),
            name.replace(".", "::"),
        }
        if "." in name:
            member = name.rsplit(".", 1)[1]
            values.update((member, member.lstrip("#")))
        return values

    def canonical_name(name, expected):
        candidate = name.removesuffix("()")
        matches = [n for n in expected if candidate in aliases(n)]
        return matches[0] if len(matches) == 1 else name

    by_path = {}
    for symbol in rubric["symbols"]:
        by_path.setdefault(symbol["path"], []).append(symbol["name"])
    symbols = {
        (s["path"], canonical_name(s["name"], by_path.get(s["path"], [])))
        for s in answer["symbols"]
    }
    endpoints = {e[key] for e in rubric["edges"] for key in ("from", "to")}
    edges = {
        (canonical_name(e["from"], endpoints), canonical_name(e["to"], endpoints))
        for e in answer["edges"]
    }
    missing_symbols = [
        s for s in rubric["symbols"] if (s["path"], s["name"]) not in symbols
    ]
    missing_edges = [e for e in rubric["edges"] if (e["from"], e["to"]) not in edges]
    missing_claims = [
        key
        for key, value in rubric["claims"].items()
        if answer["claims"].get(key)
        not in [value, *rubric.get("equivalents", {}).get(key, [])]
    ]
    return {
        "pass": not (missing_symbols or missing_edges or missing_claims),
        "reason": "rubric",
        "missing_symbols": missing_symbols,
        "missing_edges": missing_edges,
        "missing_claims": missing_claims,
    }
