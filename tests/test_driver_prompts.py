import importlib.util
from pathlib import Path


spec = importlib.util.spec_from_file_location(
    "engine_driver", Path(__file__).parents[1] / "public/engine_driver.py"
)
driver = importlib.util.module_from_spec(spec)
spec.loader.exec_module(driver)


def test_command_confirmation_with_empty_choices_is_approved():
    assert (
        driver.prompt_answer(
            {
                "kind": "confirm",
                "choices": [],
                "question": "[coder 123] Allow this command?\npython -m pytest tests/test_tables.py -q",
            }
        )
        == "yes"
    )


def test_settlement_and_continue_remain_automatic_but_mcp_auth_is_declined():
    assert (
        driver.prompt_answer(
            {"kind": "settlement", "choices": ["merge", "keep", "discard"]}
        )
        == "merge"
    )
    assert (
        driver.prompt_answer({"kind": "choice", "choices": ["continue", "stop"]})
        == "continue"
    )
    assert driver.prompt_answer({"kind": "mcp_auth", "choices": []}) == "no"
