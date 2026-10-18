from harness import ROOT
from harness.campaign import attempt
from harness.mini import main as mini_main
import inspect


def test_engine_session_keeps_worktrees():
    text = (ROOT / "public/engine_session.py").read_text()
    assert 'settle="keep"' in text
    assert "wait_until_idle" in text
    assert "/opt/engine/app.py" in text


def test_runtime_copies_engine_into_image():
    text = (ROOT / "Dockerfile").read_text()
    assert "COPY engine /opt/engine" in text


def test_mini_calls_host_proxy_not_docker_dns():
    campaign = inspect.getsource(attempt)
    mini = inspect.getsource(mini_main)
    assert "network.host_url" in campaign
    assert "research_url" in campaign
    assert "research_url" in mini
