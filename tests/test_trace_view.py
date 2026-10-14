import json

from codeloom_benchmark.trace_view import build_view, response_messages


def test_stream_fragments_keep_concurrent_calls_and_reasoning():
    deltas = [
        {
            "content": "Read ",
            "reasoning": "Plan ",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "a",
                    "function": {"name": "read", "arguments": '{"p":'},
                },
                {
                    "index": 1,
                    "id": "b",
                    "function": {"name": "bash", "arguments": '{"c":'},
                },
            ],
        },
        {
            "content": "files",
            "reasoning": "first",
            "tool_calls": [
                {"index": 1, "function": {"arguments": '"pwd"}'}},
                {"index": 0, "function": {"arguments": '"app.py"}'}},
            ],
        },
    ]
    raw = (
        "\n\n".join(
            "data: " + json.dumps({"choices": [{"delta": delta}]}) for delta in deltas
        )
        + "\n\ndata: [DONE]"
    )
    result = response_messages(raw)
    assert result["text"] == "Read files"
    assert result["reasoning"] == "Plan first"
    assert result["tool_calls"] == [
        {"id": "a", "name": "read", "arguments": '{"p":"app.py"}'},
        {"id": "b", "name": "bash", "arguments": '{"c":"pwd"}'},
    ]


def test_engine_agents_keep_same_call_ids_separate(tmp_path):
    trial = tmp_path / "trials/example"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps(
            {"id": "example", "system": "engine-core", "verification": {"pass": False}}
        )
    )
    events = [
        {
            "type": "AgentStarted",
            "agent_id": "child",
            "parent_id": "",
            "profile": "coder",
            "task": "Fix it",
        },
        {
            "type": "ChatMessageStarted",
            "agent_id": "child",
            "id": "m",
            "role": "assistant",
        },
        {
            "type": "ChatMessageDelta",
            "agent_id": "child",
            "id": "m",
            "channel": "text",
            "text": "Inspect ",
        },
        {
            "type": "ChatMessageDelta",
            "agent_id": "child",
            "id": "m",
            "channel": "text",
            "text": "source",
        },
        {
            "type": "ToolCallStarted",
            "agent_id": "child",
            "call_id": "t1",
            "name": "read_file",
            "arguments_json": "{}",
        },
        {
            "type": "ToolCallStarted",
            "agent_id": "",
            "call_id": "t1",
            "name": "search",
            "arguments_json": "{}",
        },
        {
            "type": "ToolCallFinished",
            "agent_id": "child",
            "call_id": "t1",
            "name": "read_file",
            "preview": "child output",
            "ok": True,
        },
        {
            "type": "ToolCallFinished",
            "agent_id": "",
            "call_id": "t1",
            "name": "search",
            "preview": "root error",
            "ok": False,
        },
    ]
    (trial / "engine-events.jsonl").write_text("\n".join(json.dumps(e) for e in events))
    view = build_view(tmp_path, trial)
    assert len(view["agents"]) == 2
    assert view["agents"][1]["parent_id"] == "root"
    assert (
        next(e for e in view["timeline"] if e["kind"] == "message")["detail"]
        == "Inspect source"
    )
    calls = [e for e in view["timeline"] if e["kind"] == "tool"]
    assert calls[0]["output"] == "child output" and calls[0]["ok"] is True
    assert calls[1]["output"] == "root error" and calls[1]["ok"] is False


def test_missing_trace_does_not_invent_activity(tmp_path):
    trial = tmp_path / "trials/interrupted"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps({"cost_usd": None, "termination": "infra_unresolved"})
    )
    view = build_view(tmp_path, trial)
    assert view["cost_usd"] is None
    assert view["timeline"] == [] and not view["trace_available"]
    assert view["agents"][0]["tool_calls"] == 0


def test_reservation_is_not_presented_as_actual_cost(tmp_path):
    trial = tmp_path / "trials/unknown-bill"
    trial.mkdir(parents=True)
    (trial / "result.json").write_text(
        json.dumps({"cost_usd": 1.0, "cost_is_conservative": True})
    )
    assert build_view(tmp_path, trial)["cost_usd"] is None
