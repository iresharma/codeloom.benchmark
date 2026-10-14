from codeloom_benchmark.graders.testing import assertion_signature, stable_kill


def test_import_and_runtime_errors_are_not_mutant_kills():
    report = {
        "tests": [
            {
                "nodeid": "test_new.py::test_behavior",
                "outcome": "failed",
                "call": {
                    "crash": {"message": "TypeError: invalid argument"},
                    "longrepr": "assert fn()",
                },
            }
        ]
    }
    assert assertion_signature("", "pytest", report) is None
    assert assertion_signature("not ok 1 - test\nERR_MODULE_NOT_FOUND", "node") is None


def test_mutation_requires_same_assertion_three_times():
    records = [{"returncode": 1, "assertion_signature": ["test_a"]} for _ in range(3)]
    assert stable_kill(records)
    records[-1]["assertion_signature"] = ["test_b"]
    assert not stable_kill(records)


def test_same_test_failing_on_different_lines_is_unstable():
    def report(line):
        return {
            "tests": [
                {
                    "nodeid": "test_feature.py::test_behavior",
                    "outcome": "failed",
                    "call": {
                        "crash": {"message": "AssertionError: wrong", "lineno": line}
                    },
                }
            ]
        }

    runs = [
        {
            "returncode": 1,
            "assertion_signature": assertion_signature("", "pytest", report(line)),
        }
        for line in (7, 7, 8)
    ]
    assert not stable_kill(runs)


def test_node_mixed_assertion_and_import_failure_is_not_a_kill():
    output = "not ok 1 - behavior\n  code: ERR_ASSERTION\nnot ok 2 - setup\n  error: failed to load file"
    assert assertion_signature(output, "node") is None
