"""Build offline run views from published evidence, without inference or grading."""

from __future__ import annotations

import gzip
import json
from collections import Counter
from pathlib import Path

from .artifacts import write_json


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def read_lines(path):
    return (
        [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        if path.exists()
        else []
    )


def display(value):
    return (
        value
        if isinstance(value, str)
        else json.dumps(value, indent=2, ensure_ascii=False)
    )


def response_messages(raw):
    """Reassemble streamed assistant content, reasoning and concurrent tool calls."""
    try:
        chunks = [json.loads(raw)]
    except json.JSONDecodeError:
        chunks = []
        for line in raw.splitlines():
            if line.startswith("data:") and line[5:].strip() != "[DONE]":
                chunks.append(json.loads(line[5:].strip()))
    text, reasoning, calls = [], [], {}
    for chunk in chunks:
        for choice in chunk.get("choices", []):
            message = choice.get("message") or choice.get("delta") or {}
            if message.get("content"):
                text.append(display(message["content"]))
            for key in ("reasoning_content", "reasoning"):
                if message.get(key):
                    reasoning.append(display(message[key]))
                    break
            for part in message.get("tool_calls", []):
                key = part.get("index", part.get("id", len(calls)))
                call = calls.setdefault(key, {"id": "", "name": "", "arguments": ""})
                if part.get("id"):
                    call["id"] = part["id"]
                function = part.get("function", {})
                for target, source in (("name", "name"), ("arguments", "arguments")):
                    call[target] += function.get(source) or ""
    return {
        "text": "".join(text),
        "reasoning": "".join(reasoning),
        "tool_calls": list(calls.values()),
    }


def build_view(root: Path, trial: Path, synthetic=False):
    result = read_json(trial / "result.json", {})
    agents = {
        "root": {
            "id": "root",
            "profile": "controller",
            "parent_id": None,
            "task": "",
            "status": result.get("termination", "unknown"),
        }
    }
    timeline, messages, calls = [], {}, {}

    def add(kind, agent, title, detail="", **fields):
        event = {
            "kind": kind,
            "agent_id": agent,
            "title": title,
            "detail": display(detail),
            **fields,
        }
        timeline.append(event)
        return event

    events = read_lines(trial / "engine-events.jsonl")
    for event in events:
        kind, agent = event.get("type"), event.get("agent_id") or "root"
        if kind == "AgentStarted":
            agents[agent] = {
                "id": agent,
                "profile": event["profile"],
                "parent_id": event.get("parent_id") or "root",
                "task": event.get("task", ""),
                "status": "started",
            }
            add("agent", agent, "Spawned " + event["profile"], event.get("task", ""))
        elif kind == "AgentFinished":
            agents.setdefault(
                agent,
                {
                    "id": agent,
                    "profile": event.get("profile", "agent"),
                    "parent_id": "root",
                    "task": "",
                },
            )["status"] = event.get("status", "finished")
            add("agent", agent, "Agent finished", event.get("summary", ""))
        elif kind in ("ChatMessageAdded", "ChatMessageStarted", "ChatHistoryAdded"):
            key = (agent, event["id"])
            if key not in messages:
                messages[key] = add(
                    "message",
                    agent,
                    event.get("role", "assistant"),
                    event.get("text", ""),
                    reasoning="",
                )
            elif event.get("text"):
                messages[key]["detail"] = event["text"]
        elif kind == "ChatMessageDelta":
            key = (agent, event["id"])
            if key not in messages:
                messages[key] = add("message", agent, "assistant", "", reasoning="")
            field = (
                "reasoning"
                if event.get("channel") in ("reasoning", "thinking")
                else "detail"
            )
            messages[key][field] += event.get("text", "")
        elif kind == "ToolCallStarted":
            calls[(agent, event["call_id"])] = add(
                "tool",
                agent,
                event["name"],
                event.get("arguments_json", ""),
                call_id=event["call_id"],
                output=None,
                ok=None,
                duration_ms=None,
            )
        elif kind == "ToolCallFinished":
            call = calls.get((agent, event["call_id"]))
            if call is None:
                call = add(
                    "tool",
                    agent,
                    event["name"],
                    "Arguments unavailable",
                    call_id=event["call_id"],
                )
            call.update(
                output=event.get("preview", ""),
                ok=event.get("ok"),
                duration_ms=event.get("duration_ms"),
            )
        elif kind in ("ErrorOccurred", "WarningOccurred", "SessionEnded", "FileEdited"):
            add(
                "error" if kind == "ErrorOccurred" else "event",
                agent,
                kind,
                event.get("message", event.get("diff", event.get("reason", ""))),
            )

    native = read_json(trial / "native-trace.json", {})
    if not events:
        for message in native.get("messages", []):
            role = message.get("role", "unknown")
            if role == "tool":
                call = calls.get(message.get("tool_call_id"))
                if call is not None:
                    rc = message.get("extra", {}).get("returncode")
                    call.update(
                        output=display(message.get("content", "")),
                        ok=None if rc is None else rc == 0,
                    )
                else:
                    add("message", "root", "tool result", message.get("content", ""))
            else:
                add(
                    "message",
                    "root",
                    role,
                    message.get("content", ""),
                    reasoning=message.get("reasoning_content") or "",
                )
                for tool in message.get("tool_calls", []):
                    function = tool.get("function", {})
                    calls[tool.get("id")] = add(
                        "tool",
                        "root",
                        function.get("name", "tool"),
                        function.get("arguments", ""),
                        call_id=tool.get("id"),
                        output=None,
                        ok=None,
                        duration_ms=None,
                    )
    for i, event in enumerate(timeline):
        event["sequence"] = i + 1
        agent = event["agent_id"]
        agents.setdefault(
            agent,
            {
                "id": agent,
                "profile": "agent",
                "parent_id": None,
                "task": "",
                "status": "unknown",
            },
        )
    for agent in agents.values():
        tools = Counter(
            e["title"]
            for e in timeline
            if e["kind"] == "tool" and e["agent_id"] == agent["id"]
        )
        agent["tool_calls"] = sum(tools.values())
        agent["tools"] = [
            {"name": name, "count": count} for name, count in tools.most_common()
        ]

    def blob(reference):
        if not reference:
            return ""
        path = Path(reference)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("Unsafe artifact reference")
        return gzip.decompress((root / path).read_bytes()).decode()

    requests = []
    for row in read_lines(trial / "broker.jsonl"):
        request = json.loads(blob(row.get("request_blob")) or "{}")
        response = response_messages(blob(row.get("response_blob")) or "{}")
        requests.append(
            {
                "id": row.get("id"),
                "model": request.get("model"),
                "reasoning_effort": request.get("reasoning", {}).get("effort"),
                "status": row.get("status"),
                "elapsed_s": row.get("elapsed_s"),
                "first_byte_s": row.get("first_byte_s"),
                "admission_queue_s": row.get("admission_queue_s"),
                "usage": row.get("usage"),
                "providers": row.get("returned_providers", []),
                "message_count": len(request.get("messages", [])),
                "response": response,
            }
        )
    checks = [
        {
            "kind": c.get("kind", "trusted"),
            "command": c.get("command", ""),
            "passed": c.get("returncode") == 0,
            "output": c.get("output", ""),
            "repeat": c.get("repeat", 0),
        }
        for c in result.get("verification", {}).get("checks", [])
    ]
    view = {
        "id": result.get("id", trial.name),
        "task_id": result.get("task_id", "unknown"),
        "system": result.get("system", "unknown"),
        "stage": result.get("stage", "unknown"),
        "synthetic": synthetic,
        "termination": result.get("termination", "unknown"),
        "passed": result.get("verification", {}).get("pass", False),
        "grading": result.get("verification", {}),
        "cost_usd": None
        if result.get("cost_is_conservative")
        else result.get("cost_usd"),
        "elapsed_s": result.get("elapsed_s"),
        "retry_of": result.get("retry_of"),
        "agents": list(agents.values()),
        "timeline": timeline,
        "requests": requests,
        "checks": checks,
        "patch": (trial / "patch.diff").read_text()
        if (trial / "patch.diff").exists()
        else "",
        "trace_available": bool(events or native),
        "tool_output_is_preview": bool(events),
    }
    if "sk-or-v1-" in json.dumps(view):
        raise ValueError("Credential marker in run view")
    return view


def export_run_views(root: Path, run_id: str, base_url: str, synthetic=False):
    rows = []
    for trial in sorted((root / "trials").glob("*")):
        if not (trial / "result.json").exists():
            continue
        view = build_view(root, trial, synthetic)
        write_json(trial / "view.json", view)
        rows.append(
            {
                key: view[key]
                for key in (
                    "id",
                    "task_id",
                    "system",
                    "stage",
                    "synthetic",
                    "termination",
                    "passed",
                    "cost_usd",
                    "elapsed_s",
                    "retry_of",
                    "trace_available",
                )
            }
            | {
                "run_id": run_id,
                "artifact_base": f"{base_url}/trials/{trial.name}",
                "tool_calls": sum(a["tool_calls"] for a in view["agents"]),
                "agent_count": len(view["agents"]),
                "request_count": len(view["requests"]),
            }
        )
    return rows
