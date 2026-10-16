from __future__ import annotations

import json
import re
import shlex
import shutil
import sys
import tempfile
import time
from pathlib import Path

from . import ROOT, write_json
from .docker import execute, replace_task, start_container, stop
from .snapshot import apply_recipe, output_changes, snapshot, stage_output
from .tasks import oracle_dir

sys.path.insert(0, str(ROOT / "public"))
from validate_answer import validate  # noqa: E402


def generated_command(task):
    return {
        "tinydb": (
            "python -m pytest -o addopts= -q benchmark_tests --json-report --json-report-file=/tmp/generated.json",
            "pytest",
        ),
        "dotenv": (
            "python -m pytest -q benchmark_tests --json-report --json-report-file=/tmp/generated.json",
            "pytest",
        ),
        "pqueue": (
            "node --import=tsx/esm --test --test-reporter=tap benchmark_tests/*.ts",
            "node",
        ),
        "chi": ("go test -json -count=1 ./benchmark_tests/...", "go"),
    }[task["repo"]]


def assertion_signature(text: str, framework: str, report: dict | None = None):
    if framework == "pytest" and report:
        if report.get("collectors") and any(c.get("outcome") == "failed" for c in report["collectors"]):
            return None
        failures = [t for t in report.get("tests", []) if t.get("outcome") == "failed"]
        if not failures:
            return None
        for test in failures:
            message = test.get("call", {}).get("crash", {}).get("message", "")
            if not message.startswith(
                ("AssertionError", "assert ", "Failed: DID NOT RAISE", "Failed: ")
            ):
                return None
        return tuple(sorted((t["nodeid"], t["call"]["crash"].get("lineno")) for t in failures))
    forbidden = ("SyntaxError", "ERR_MODULE_NOT_FOUND", "Cannot find module", "build failed", "setup failed", "panic:")
    if any(marker in text for marker in forbidden):
        return None
    if framework in ("node", "ava"):
        failed = list(re.finditer(r"^\s*not ok \d+\s*-?\s*(.+)$", text, re.M))
        signatures = []
        for i, match in enumerate(failed):
            end = failed[i + 1].start() if i + 1 < len(failed) else len(text)
            block = text[match.end() : end]
            if "subtestsFailed" in block:
                continue
            if not any(m in block for m in ("ERR_ASSERTION", "AssertionError", "assertion failed")):
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


def grade_navigation(task: dict, source: Path) -> dict:
    try:
        answer = json.loads((source / "navigation-answer.json").read_text())
    except (ValueError, OSError) as exc:
        return {"pass": False, "reason": "invalid_answer", "detail": str(exc)}
    errors = validate(answer)
    if errors or answer.get("task_id") != task["id"]:
        return {"pass": False, "reason": "invalid_answer", "errors": errors}
    rubric = json.loads((oracle_dir(task) / "rubric.json").read_text())

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
    missing_symbols = [s for s in rubric["symbols"] if (s["path"], s["name"]) not in symbols]
    missing_edges = [e for e in rubric["edges"] if (e["from"], e["to"]) not in edges]
    missing_claims = [
        key
        for key, value in rubric["claims"].items()
        if answer["claims"].get(key) not in [value, *rubric.get("equivalents", {}).get(key, [])]
    ]
    return {
        "pass": not (missing_symbols or missing_edges or missing_claims),
        "reason": "rubric",
        "missing_symbols": missing_symbols,
        "missing_edges": missing_edges,
        "missing_claims": missing_claims,
    }


def oracle_command(task, container):
    private = oracle_dir(task)
    from . import run

    run(["docker", "cp", str(private) + "/.", container + ":/tmp/oracle"])
    if task.get("suite") == "v2":
        return task["grader"]["command"]
    if task["language"] == "python":
        return "python -m pytest -o addopts= -q /tmp/oracle/check.py"
    if task["language"] == "typescript":
        return "node --import=tsx/esm /tmp/oracle/check.ts"
    run(["docker", "cp", str(private / "check_test.go"), container + ":/task/benchmark_oracle_test.go"])
    return "go test -count=1 -run TestBenchmarkReset ."


def _start_services(container, task) -> dict | None:
    for service in (task.get("runtime") or {}).get("services", []):
        start = execute(
            container,
            "nohup bash -lc " + shlex.quote(service["command"]) + " >/tmp/service.log 2>&1 </dev/null &",
        )
        if start.returncode:
            return {"pass": False, "reason": "service_start_failed", "checks": []}
        ready = False
        for _ in range(service.get("readiness_attempts", 60)):
            if execute(container, service["ready"], timeout=10).returncode == 0:
                ready = True
                break
            time.sleep(0.5)
        if not ready:
            logs = execute(container, "tail -80 /tmp/service.log")
            return {
                "pass": False,
                "reason": "service_unavailable",
                "checks": [],
                "detail": logs.stdout,
            }
    return None


def checks(image, task, source: Path, trusted=True, oracle=True):
    container = start_container(image, evaluator=True, resources=task.get("runtime"))
    results = []
    try:
        replace_task(container, source, (task.get("runtime") or {}).get("node_workdir"))
        failed = _start_services(container, task)
        if failed:
            return failed
        commands = list(task["verification"]) if trusted else []
        if oracle and task["category"] not in ("navigation", "test-generation"):
            commands.append(oracle_command(task, container))
        for cmd in commands:
            result = execute(container, cmd, timeout=1200)
            results.append(
                {
                    "command": cmd,
                    "kind": "trusted" if cmd in task["verification"] else "hidden",
                    "repeat": 0,
                    "returncode": result.returncode,
                    "output": (result.stdout + result.stderr)[-30000:],
                }
            )
            if result.returncode != 0:
                return {"pass": False, "checks": results}
        return {"pass": all(r["returncode"] == 0 for r in results), "checks": results}
    finally:
        stop(container)


def _run_generated(container, task):
    cmd, framework = generated_command(task)
    result = execute(container, cmd)
    report = None
    if framework == "pytest":
        dumped = execute(container, "cat /tmp/generated.json")
        if dumped.returncode == 0:
            report = json.loads(dumped.stdout)
    text = result.stdout + result.stderr
    if framework == "pytest":
        count = report.get("summary", {}).get("total", 0) if report else 0
    elif framework == "go":
        count = sum('"Action":"run"' in line and '"Test":' in line for line in text.splitlines())
    else:
        count = len(re.findall(r"^\s*(?:not )?ok \d+", text, re.M))
    return {
        "returncode": result.returncode,
        "executed_tests": count,
        "output": text[-30000:],
        "assertion_signature": assertion_signature(text, framework, report),
    }


def grade(image, task, candidate: Path, output: Path) -> dict:
    with tempfile.TemporaryDirectory(prefix="harness-eval-") as tmp:
        baseline = snapshot(task, Path(tmp) / "baseline")
        changes, disallowed = output_changes(baseline, candidate, task)
        if disallowed:
            result = {"pass": False, "reason": "disallowed_edit", "paths": disallowed}
        elif task["category"] == "navigation":
            result = grade_navigation(task, candidate)
        else:
            source = stage_output(baseline, changes, Path(tmp) / "source")
            result = checks(image, task, source)
            result["reason"] = result.get("reason", "verification")
            if task["category"] == "test-generation" and result.get("pass"):
                container = start_container(image, evaluator=True)
                try:
                    replace_task(container, source, (task.get("runtime") or {}).get("node_workdir"))
                    correct = [_run_generated(container, task) for _ in range(3)]
                    result["correct_runs"] = correct
                    if not all(r["returncode"] == 0 and r["executed_tests"] > 0 for r in correct):
                        result.update({"pass": False, "reason": "unstable_or_empty_tests"})
                    else:
                        mutations = []
                        for index, recipe in enumerate(task.get("mutants") or []):
                            mutated = Path(tmp) / f"mutant-{index}"
                            shutil.copytree(source, mutated)
                            apply_recipe(mutated, recipe)
                            replace_task(
                                container,
                                mutated,
                                (task.get("runtime") or {}).get("node_workdir"),
                            )
                            runs = [_run_generated(container, task) for _ in range(3)]
                            mutations.append({"index": index, "killed": stable_kill(runs), "runs": runs})
                        score = sum(m["killed"] for m in mutations) / len(mutations) if mutations else 0
                        result.update(mutations=mutations, mutation_score=score, **{"pass": score >= 0.8})
                finally:
                    stop(container)
        write_json(output, result)
        return result
