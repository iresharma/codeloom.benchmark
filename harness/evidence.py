"""Tool-evidence checks for realistic browser, research and terminal tasks."""

from __future__ import annotations

import json
import re
from pathlib import Path

# A search-engine host reached directly (bypassing the host-mediated research
# gateway, which only ever proxies `github`) is accepted as equivalent to a
# successful dedicated `web_search` call: once `search`/`fetch` route through
# the container's own native network transport instead of the gateway, no
# host-side research/*.json record is ever written for them, so that log is
# no longer a complete signal on its own.
_SEARCH_ENGINE_URL = re.compile(
    r"https?://(?:[\w.-]*\.)?(?:google|bing|duckduckgo)\.[a-z.]+/(?:search|html)\b",
    re.I,
)


def grade_evidence(task, trial: Path, system: str, view: dict) -> dict:
    required = task.get("evidence") or {}
    if not required:
        return {"pass": True, "checks": {}, "required": required}
    tools = [event for event in view.get("timeline", []) if event.get("kind") == "tool"]
    agents = view.get("agents") or {}
    if isinstance(agents, list):
        roles = {a["id"]: a.get("profile") for a in agents}
    else:
        roles = {key: a.get("profile") for key, a in agents.items()}
    checks = {}

    def successful(event):
        return event.get("ok") is True and not str(event.get("output", "")).lower().startswith("error:")

    if required.get("browser"):
        shots = list((trial / "browser").rglob("*.jpg")) + list((trial / "browser").rglob("*.png"))
        if system == "engine-core":
            observed = {
                event["title"]
                for event in tools
                if roles.get(event.get("agent_id")) == "tester" and successful(event)
            }
            checks["tester_browser"] = {
                "browser_open",
                "browser_screenshot",
                "browser_console",
                "browser_network",
            }.issubset(observed)
        else:
            checks["browser_command"] = any(
                successful(event)
                and "browser_check.py" in event.get("detail", "")
                and "127.0.0.1" in event.get("detail", "")
                for event in tools
            )
        checks["screenshots"] = any(path.stat().st_size > 100 for path in shots)
    if required.get("research"):
        records = [
            json.loads(path.read_text()) for path in sorted((trial / "research").glob("*.json"))
        ]
        successful_records = [row for row in records if row.get("ok") and row.get("sha256")]

        def native_search(event):
            return (
                successful(event)
                and event.get("title") in ("web_search", "web_fetch")
                and _SEARCH_ENGINE_URL.search(event.get("detail", "") or "")
            )

        checks["web_search"] = any(
            row["operation"] == "search" for row in successful_records
        ) or any(native_search(event) for event in tools)
        sources = [
            row
            for row in successful_records
            if row["operation"] == "github"
            and row.get("commit")
            and "/contents/" in (row.get("url") or "")
        ]
        checks["github_source"] = bool(sources)
        artifact = trial / "checkout" / "research.md"
        report = artifact.read_text() if artifact.exists() else ""
        checks["source_linked_report"] = bool(
            report.strip() and any(row["commit"] in report for row in sources) and "https://" in report
        )
        if system == "engine-core":
            observed = {
                event["title"]
                for event in tools
                if roles.get(event.get("agent_id")) == "researcher" and successful(event)
            }
            researcher_searched = "web_search" in observed or any(
                native_search(event)
                for event in tools
                if roles.get(event.get("agent_id")) == "researcher"
            )
            checks["researcher_role"] = researcher_searched and "github_file" in observed
    if required.get("terminal"):
        pattern = required.get("command_pattern", r"(?:python\s+-m\s+httpie|\bhttp(?:ie)?\b)")
        checks["terminal_execution"] = any(
            successful(event)
            and re.search(pattern, event.get("detail", ""))
            and (system != "engine-core" or "exit code: 0" in str(event.get("output", "")))
            for event in tools
        )
    return {"pass": bool(checks) and all(checks.values()), "checks": checks, "required": required}
