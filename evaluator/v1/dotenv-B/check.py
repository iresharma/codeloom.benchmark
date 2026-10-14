from io import StringIO
from dotenv.main import DotEnv


def test_override_and_empty(monkeypatch):
    monkeypatch.setenv("A", "outer")
    assert (
        DotEnv(None, StringIO("A=inner\nB=${A}"), override=False).dict()["B"] == "outer"
    )
    assert (
        DotEnv(None, StringIO("A=inner\nB=${A}"), override=True).dict()["B"] == "inner"
    )
    monkeypatch.setenv("A", "")
    assert DotEnv(None, StringIO("A=inner\nB=${A}"), override=False).dict()["B"] == ""
