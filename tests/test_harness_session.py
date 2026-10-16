from harness import ROOT


def test_engine_session_keeps_worktrees():
    text = (ROOT / "public/engine_session.py").read_text()
    assert 'settle="keep"' in text
    assert "wait_until_idle" in text
    assert "/opt/engine/app.py" in text
