from harness.tasks import load_suite


def test_classic_suite_has_light_medium_heavy():
    tasks = load_suite("classic")
    mix = {row["difficulty"] for row in tasks}
    assert mix == {"light", "medium", "heavy"}
    assert len(tasks) == 10
    ids = {row["id"] for row in tasks}
    assert {"tinydb-B", "chi-N", "dotenv-X", "pqueue-T"} <= ids


def test_realistic_suite_is_all_heavy():
    tasks = load_suite("realistic")
    assert len(tasks) == 15
    assert {row["difficulty"] for row in tasks} == {"heavy"}
    assert any(row["category"] == "browser" for row in tasks)
    assert any(row["category"] == "research" for row in tasks)


def test_task_filter():
    tasks = load_suite("classic", only=["tinydb-B", "chi-N"])
    assert [row["id"] for row in tasks] == ["tinydb-B", "chi-N"]
