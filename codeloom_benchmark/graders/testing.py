"""Only repeatable assertion failures count as behavioral mutant kills."""

import json
import re


def assertion_signature(text: str, framework: str, report: dict | None = None):
    if framework == "pytest" and report:
        failures = [t for t in report.get("tests", []) if t.get("outcome") == "failed"]
        if report.get("collectors") and any(
            c.get("outcome") == "failed" for c in report["collectors"]
        ):
            return None
        if not failures:
            return None
        for t in failures:
            message = t.get("call", {}).get("crash", {}).get("message", "")
            if not message.startswith(
                ("AssertionError", "assert ", "Failed: DID NOT RAISE", "Failed: ")
            ):
                return None
        return tuple(
            sorted((t["nodeid"], t["call"]["crash"].get("lineno")) for t in failures)
        )
    forbidden = (
        "SyntaxError",
        "ERR_MODULE_NOT_FOUND",
        "Cannot find module",
        "build failed",
        "setup failed",
        "panic:",
    )
    if any(x in text for x in forbidden):
        return None
    if framework in ("node", "ava"):
        failed = list(re.finditer(r"^\s*not ok \d+\s*-?\s*(.+)$", text, re.M))
        signatures = []
        for i, match in enumerate(failed):
            end = failed[i + 1].start() if i + 1 < len(failed) else len(text)
            block = text[match.end() : end]
            if "subtestsFailed" in block:
                continue
            if not any(
                marker in block
                for marker in ("ERR_ASSERTION", "AssertionError", "assertion failed")
            ):
                return None
            site = re.search(r"(benchmark_tests/[^\s:]+:\d+(?::\d+)?)", block)
            signatures.append((match.group(1), site.group(1) if site else None))
        if signatures:
            return tuple(sorted(signatures))
    if framework == "go":
        failed = []
        for line in text.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get("Action") == "fail" and event.get("Test"):
                failed.append(event["Test"])
        if failed and "FAIL" in text:
            return tuple(sorted(failed))
    return None


def stable_kill(results):
    signatures = [r.get("assertion_signature") for r in results]
    return (
        len(results) == 3
        and all(r["returncode"] != 0 for r in results)
        and bool(signatures[0])
        and all(s == signatures[0] for s in signatures)
    )
