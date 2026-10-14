from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from .artifacts import write_json
from .fixtures import apply_recipe, command, output_changes, snapshot, stage_output
from .graders import navigation
from .graders.testing import assertion_signature, stable_kill
from .manifests import ROOT
from .sandbox import execute, remove, replace_task, start_container


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
        "ky": ("./node_modules/.bin/ava --tap benchmark_tests/*.ts", "ava"),
        "chi": ("go test -json -count=1 ./benchmark_tests/...", "go"),
    }[task["repo"]]


def run_generated(cid, task):
    cmd, framework = generated_command(task)
    result = execute(cid, cmd)
    report = None
    if framework == "pytest":
        r = execute(cid, "cat /tmp/generated.json")
        if r.returncode == 0:
            report = json.loads(r.stdout)
    text = result.stdout + result.stderr
    if framework == "pytest":
        count = report.get("summary", {}).get("total", 0) if report else 0
    elif framework == "go":
        count = sum(
            '"Action":"run"' in line and '"Test":' in line for line in text.splitlines()
        )
    else:
        count = len(
            __import__("re").findall(r"^\s*(?:not )?ok \d+", text, __import__("re").M)
        )
    return {
        "returncode": result.returncode,
        "executed_tests": count,
        "output": text[-30000:],
        "assertion_signature": assertion_signature(text, framework, report),
    }


def oracle_command(task, cid):
    private = (
        ROOT
        / "evaluator"
        / ("dev" if task["id"].startswith("dev-") else "v1")
        / task["id"]
    )
    command(["docker", "cp", str(private) + "/.", cid + ":/tmp/oracle"])
    if task["language"] == "python":
        return "python -m pytest -o addopts= -q /tmp/oracle/check.py"
    if task["language"] == "typescript":
        return "node --import=tsx/esm /tmp/oracle/check.ts"
    command(
        [
            "docker",
            "cp",
            str(private / "check_test.go"),
            cid + ":/task/benchmark_oracle_test.go",
        ]
    )
    return "go test -count=1 -run TestBenchmarkReset ."


def checks(image, task, source: Path, trusted=True, oracle=True, repetitions=1):
    cid = start_container(image, evaluator=True)
    results = []
    try:
        replace_task(cid, source)
        commands = list(task["verification"]) if trusted else []
        if oracle and task["category"] in ("bug", "cross-file"):
            commands.append(oracle_command(task, cid))
        for repeat in range(repetitions):
            for cmd in commands:
                r = execute(cid, cmd, timeout=1200)
                results.append(
                    {
                        "command": cmd,
                        "kind": "trusted" if cmd in task["verification"] else "hidden",
                        "repeat": repeat,
                        "returncode": r.returncode,
                        "output": (r.stdout + r.stderr)[-30000:],
                    }
                )
                if r.returncode != 0:
                    return {"pass": False, "checks": results}
        return {"pass": all(r["returncode"] == 0 for r in results), "checks": results}
    finally:
        remove(cid)


def grade(image, task, candidate: Path, output: Path):
    with tempfile.TemporaryDirectory(prefix="benchmark-eval-") as d:
        baseline = snapshot(task, Path(d) / "baseline")
        changes, disallowed = output_changes(baseline, candidate, task)
        if disallowed:
            result = {"pass": False, "reason": "disallowed_edit", "paths": disallowed}
        elif task["category"] == "navigation":
            result = navigation.grade(task, candidate)
        else:
            source = stage_output(baseline, changes, Path(d) / "source")
            result = checks(image, task, source)
            result["reason"] = "verification"
            if task["category"] == "test-generation" and result["pass"]:
                cid = start_container(image, evaluator=True)
                try:
                    replace_task(cid, source)
                    correct = [run_generated(cid, task) for _ in range(3)]
                    result["correct_runs"] = correct
                    if not all(
                        r["returncode"] == 0 and r["executed_tests"] > 0
                        for r in correct
                    ):
                        result.update(
                            {"pass": False, "reason": "unstable_or_empty_tests"}
                        )
                    else:
                        mutations = []
                        for index, recipe in enumerate(task["mutants"]):
                            mutated = Path(d) / f"mutant-{index}"
                            shutil.copytree(source, mutated)
                            apply_recipe(mutated, recipe)
                            replace_task(cid, mutated)
                            runs = [run_generated(cid, task) for _ in range(3)]
                            mutations.append(
                                {
                                    "index": index,
                                    "killed": stable_kill(runs),
                                    "runs": runs,
                                }
                            )
                        score = sum(m["killed"] for m in mutations) / len(mutations)
                        result.update(
                            mutations=mutations,
                            mutation_score=score,
                            **{"pass": score >= 0.8},
                        )
                finally:
                    remove(cid)
        write_json(output, result)
        return result


def certify(task, image):
    root = ROOT / ".cache/certification" / task["id"]
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    base = snapshot(task, root / "base")
    reference = snapshot(task, root / "reference", reference=True)
    record = {"id": task["id"], "image": image, "status": "failed"}
    if task["category"] == "navigation":
        record["reference"] = navigation.grade(task, reference)
        record["noop"] = navigation.grade(task, base)
        record["status"] = (
            "certified"
            if record["reference"]["pass"] and not record["noop"]["pass"]
            else "failed"
        )
    elif task["category"] == "test-generation":
        record["noop"] = grade(image, task, base, root / "noop-grade.json")
        record["reference_regressions"] = checks(
            image, task, reference, oracle=False, repetitions=5
        )
        record["reference"] = grade(
            image, task, reference, root / "reference-grade.json"
        )
        record["mutants_survive_trusted"] = []
        for i, recipe in enumerate(task["mutants"]):
            mutant = root / f"baseline-mutant-{i}"
            shutil.copytree(base, mutant)
            apply_recipe(mutant, recipe)
            record["mutants_survive_trusted"].append(
                checks(image, task, mutant, oracle=False)
            )
        record["status"] = (
            "certified"
            if record["reference"]["pass"]
            and not record["noop"]["pass"]
            and record["reference_regressions"]["pass"]
            and all(m["pass"] for m in record["mutants_survive_trusted"])
            else "failed"
        )
    else:
        record["noop"] = checks(image, task, base)
        record["noop_hidden"] = checks(image, task, base, trusted=False)
        record["reference"] = checks(image, task, reference, repetitions=5)
        record["status"] = (
            "certified"
            if not record["noop_hidden"]["pass"] and record["reference"]["pass"]
            else "failed"
        )
    from .manifests import fingerprint

    record["suite_fingerprint"] = fingerprint()[0]
    write_json(root / "certification.json", record)
    return record
