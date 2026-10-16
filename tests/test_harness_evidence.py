from harness.evidence import grade_evidence
from harness.tasks import load_suite


def test_browser_evidence_requires_tester_tools_and_screenshot(tmp_path):
    task = next(row for row in load_suite("realistic") if row["id"] == "memos-search")
    browser = tmp_path / "browser"
    browser.mkdir()
    (browser / "shot.png").write_bytes(b"\x89PNG" + b"0" * 200)
    view = {
        "agents": [{"id": "t1", "profile": "tester", "tool_calls": 4}],
        "timeline": [
            {"kind": "tool", "agent_id": "t1", "title": name, "ok": True, "output": "ok", "detail": ""}
            for name in ("browser_open", "browser_screenshot", "browser_console", "browser_network")
        ],
    }
    result = grade_evidence(task, tmp_path, "engine-core", view)
    assert result["pass"]
    mini = grade_evidence(
        task,
        tmp_path,
        "mini-swe-agent",
        {
            "agents": [{"id": "root", "profile": "controller", "tool_calls": 1}],
            "timeline": [
                {
                    "kind": "tool",
                    "agent_id": "root",
                    "title": "shell",
                    "ok": True,
                    "detail": "python /opt/benchmark-public/browser_check.py http://127.0.0.1:3001/reader",
                    "output": "ok",
                }
            ],
        },
    )
    assert mini["pass"]


def test_research_evidence_needs_search_github_and_report(tmp_path):
    task = next(row for row in load_suite("realistic") if row["id"] == "httpx-redirects")
    research = tmp_path / "research"
    research.mkdir()
    (research / "0000.json").write_text(
        '{"ok": true, "operation": "search", "sha256": "abc", "commit": null, "url": "https://example"}'
    )
    (research / "0001.json").write_text(
        '{"ok": true, "operation": "github", "sha256": "def", "commit": "26d48e0634e6ee9cdc0533996db289ce4b430177", "url": "https://api.github.com/repos/encode/httpx/contents/httpx/_client.py"}'
    )
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "research.md").write_text(
        "See 26d48e0634e6ee9cdc0533996db289ce4b430177 at https://github.com/encode/httpx\n"
    )
    view = {
        "agents": [{"id": "r1", "profile": "researcher", "tool_calls": 2}],
        "timeline": [
            {"kind": "tool", "agent_id": "r1", "title": "web_search", "ok": True, "output": "ok", "detail": ""},
            {"kind": "tool", "agent_id": "r1", "title": "github_file", "ok": True, "output": "ok", "detail": ""},
        ],
    }
    result = grade_evidence(task, tmp_path, "engine-core", view)
    assert result["pass"]
