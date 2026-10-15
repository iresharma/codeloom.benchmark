from codeloom_benchmark.reporting import summarize


def manifest():
    return {
        "run_id": "test",
        "settings": {"model": "model", "provider": "provider"},
        "tasks": [
            {"id": "tinydb-B", "category": "bug"},
            {"id": "chi-N", "category": "navigation"},
        ],
    }


def attempt(id, task, system, termination, passed, cost, retry_of=None):
    return {
        "id": id,
        "task_id": task,
        "system": system,
        "stage": "recovery" if retry_of else "measured",
        "termination": termination,
        "verification": {"pass": passed},
        "cost_usd": cost,
        "elapsed_s": 10,
        "retry_of": retry_of,
    }


def test_recovery_is_one_vote_but_all_charges_count():
    records = [
        attempt("1", "tinydb-B", "mini-swe-agent", "provider_error", False, 0.1),
        attempt("2", "tinydb-B", "mini-swe-agent", "completed", True, 0.2, "1"),
        attempt("3", "tinydb-B", "engine-core", "completed", False, 0.4),
    ]
    summary = summarize(manifest(), records)
    assert summary["pairs"]["mini_only"] == 1
    assert summary["pairs"]["unrun"] == 1
    mini = summary["systems"][0]
    assert mini["passed"] == mini["evaluated"] == 1
    assert round(mini["cost_per_success_usd"], 2) == 0.3


def test_budget_exhausted_termination_with_passing_verification_counts_as_success():
    # A harness-side admission rejection (a budget_pending race between a trial's
    # own calls, a cascaded unknown-bill halt, ...) can stomp a "completed"
    # termination with "budget_exhausted" even when the agent's own work already
    # finished and the delivered artifact genuinely passes grading. The artifact's
    # own verification is the real signal of success, not termination noise
    # around it. Confirmed against two live v1 trials (dotenv-X and pqueue-X,
    # engine-core) whose agents finished and passed despite a harness-side
    # rejection stomping their termination label.
    summary = summarize(
        manifest(),
        [attempt("1", "tinydb-B", "engine-core", "budget_exhausted", True, None)],
    )
    engine = summary["systems"][1]
    assert engine["passed"] == 1
    assert engine["evaluated"] == 1
    # Cost still reads as unknown: a termination other than "completed" means
    # the settled charge for this attempt isn't trustworthy as a total.
    assert engine["cost_per_success_usd"] is None
    assert engine["cost_usd"] is None
    assert engine["budget_hits"] == 1


def test_unresolved_attempt_with_no_checkout_is_neither_pass_nor_fail():
    unresolved = attempt("1", "tinydb-B", "engine-core", "watchdog", False, None)
    unresolved["verification"] = {"pass": False, "reason": "no_checkout"}
    summary = summarize(manifest(), [unresolved])
    engine = summary["systems"][1]
    assert engine["passed"] == 0
    assert engine["evaluated"] == 0
    assert engine["unresolved"] == 1


def test_unsealed_attempt_is_visible_with_unknown_bill_and_time(tmp_path):
    import json
    from codeloom_benchmark.reporting import load_attempts

    trial = tmp_path / "trials/interrupted"
    trial.mkdir(parents=True)
    (trial / "attempt.json").write_text(
        json.dumps(
            {
                "id": "interrupted",
                "task_id": "tinydb-B",
                "system": "mini-swe-agent",
                "stage": "measured",
                "retry_of": None,
            }
        )
    )
    summary = summarize(manifest(), load_attempts(tmp_path))
    mini = summary["systems"][0]
    assert summary["status"] == "partial"
    assert mini["unresolved"] == 1 and mini["unrun"] == 1
    assert mini["cost_usd"] is None
    assert mini["median_elapsed_s"] is None
    assert summary["tasks"][0]["systems"]["mini-swe-agent"]["elapsed_s"] is None
