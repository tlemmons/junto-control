from __future__ import annotations

import json
from types import SimpleNamespace

from claudecontrol.mcp_client import REQUIRED_TOOLS, Capabilities, _unwrap_tool_result


def test_required_tools_match_spec() -> None:
    expected = {
        "memory_start_session",
        "memory_send_message",
        "memory_get_messages",
        "memory_acknowledge_message",
        "memory_list_agents",
        "memory_get_spec",
        "memory_list_backlog",
    }
    assert set(REQUIRED_TOOLS) == expected


def test_capabilities_missing_required() -> None:
    caps = Capabilities(tools={"memory_send_message", "memory_get_messages"})
    missing = caps.missing_required()
    assert "memory_start_session" in missing
    assert "memory_send_message" not in missing


def test_unwrap_tool_result_double_encoded() -> None:
    inner = {"session_id": "abc", "auth": {"role": "user"}}
    outer = {"result": json.dumps(inner)}
    fake = SimpleNamespace(content=[SimpleNamespace(text=json.dumps(outer))])
    assert _unwrap_tool_result(fake) == inner


def test_unwrap_tool_result_plain() -> None:
    payload = {"foo": 1}
    fake = SimpleNamespace(content=[SimpleNamespace(text=json.dumps(payload))])
    assert _unwrap_tool_result(fake) == payload


def test_unwrap_tool_result_empty() -> None:
    fake = SimpleNamespace(content=[])
    assert _unwrap_tool_result(fake) == {}


def test_unwrap_tool_result_non_json() -> None:
    fake = SimpleNamespace(content=[SimpleNamespace(text="oops")])
    assert _unwrap_tool_result(fake) == {"raw": "oops"}
