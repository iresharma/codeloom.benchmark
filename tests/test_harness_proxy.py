import pytest

from harness.proxy import BudgetRefused, Proxy
from harness.settings import MODEL


def test_issue_and_revoke(tmp_path):
    proxy = Proxy("sk-test", tmp_path)
    token = proxy.issue("tinydb-B-engine-core", 1.0)
    assert proxy._trial(token)["cap"] == 1.0
    proxy.revoke(token)
    with pytest.raises(BudgetRefused):
        proxy._trial(token)


def test_budget_stop_and_call_gate(tmp_path):
    proxy = Proxy("sk-test", tmp_path)
    token = proxy.issue("t1", 1.0)
    trial = proxy._trial(token)
    body, estimate = proxy.prepare(
        {"model": MODEL, "messages": [{"role": "user", "content": "hi"}], "reasoning_effort": "high"}
    )
    assert body["model"] == MODEL
    assert body["provider"]["only"] == ["baidu/fp8"]
    proxy.admit(trial, 0.5)
    proxy.charge("t1", 1.0)
    with pytest.raises(BudgetRefused, match="reached the cap"):
        proxy.admit(trial, 0.01)
    with pytest.raises(BudgetRefused, match="3x"):
        proxy.admit({"id": "t2", "cap": 1.0}, 10)


def test_unknown_model_is_rejected(tmp_path):
    proxy = Proxy("sk-test", tmp_path)
    with pytest.raises(BudgetRefused):
        proxy.prepare({"model": "openai/gpt-4", "messages": [{"role": "user", "content": "x"}]})
