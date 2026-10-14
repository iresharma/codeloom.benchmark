"""Public format validator. Deliberately has no expected answers."""

import json
import sys
from pathlib import Path


def validate(data):
    errors = []
    if not isinstance(data, dict) or set(data) != {
        "task_id",
        "symbols",
        "edges",
        "claims",
    }:
        return ["Expected exactly task_id, symbols, edges, claims"]
    if not isinstance(data["task_id"], str):
        errors.append("task_id must be a string")
    if not isinstance(data["symbols"], list):
        errors.append("symbols must be an array")
    else:
        for s in data["symbols"]:
            if not isinstance(s, dict) or set(s) != {"path", "name"}:
                errors.append("Each symbol needs path and name")
            elif (
                any(not isinstance(v, str) or not v for v in s.values())
                or s["path"].startswith("/")
                or ".." in Path(s["path"]).parts
            ):
                errors.append(
                    "Use nonempty names and relative paths without parent traversal"
                )
    if not isinstance(data["edges"], list) or any(
        not isinstance(e, dict)
        or set(e) != {"from", "to"}
        or any(not isinstance(v, str) for v in e.values())
        for e in data["edges"]
    ):
        errors.append("Each edge needs string from and to")
    if not isinstance(data["claims"], dict) or any(
        not isinstance(v, str) or not v for v in data["claims"].values()
    ):
        errors.append("claims must map claim IDs to nonempty strings")
    return errors


if __name__ == "__main__":
    try:
        errors = validate(
            json.loads(
                Path(
                    sys.argv[1] if len(sys.argv) > 1 else "navigation-answer.json"
                ).read_text()
            )
        )
    except (ValueError, OSError) as exc:
        errors = [str(exc)]
    print(
        "\n".join(errors)
        if errors
        else "Answer format valid (correctness is not checked)."
    )
    sys.exit(bool(errors))
