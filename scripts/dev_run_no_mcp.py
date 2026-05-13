"""
Dev-mode run that bypasses real MCP connection so we can render-test the UI.
Uses a stub MCPClient that pretends to be connected and returns canned data.

Note: this script registers ONLY the web router (login/projects/inbox).
The /healthz, /api/project/select, /ws/inbox endpoints are wired in
src/juntocontrol/main.py:create_app and not exercised here. To do a full
production-style run, set TOM_WEB_API_KEY + SESSION_SECRET + LOGIN_PASSPHRASE
in .env and run:  python -m juntocontrol.main

Run:  .venv/bin/python scripts/dev_run_no_mcp.py
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import structlog
import uvicorn
from fastapi import FastAPI

from juntocontrol.auth import SessionStore
from juntocontrol.config import Settings
from juntocontrol.inbox import InboxBroker
from juntocontrol.web import build_router

log = structlog.get_logger("dev_run")


class StubMCP:
    def __init__(self) -> None:
        self.connected = True
        self.session_id = "stub_session"
        self.last_connected_at = 0
        self.capabilities = SimpleNamespace(
            tools={
                "memory_start_session", "memory_send_message", "memory_get_messages",
                "memory_acknowledge_message", "memory_list_agents", "memory_get_spec",
                "memory_list_backlog",
            },
            resources_subscribe=False,
            inbox_resource_supported=True,
            missing_required=lambda: set(),
        )

    async def call(self, tool: str, **kwargs: Any) -> Any:
        if tool == "memory_list_agents":
            all_agents = [
                {"project": "nimbus", "instance": "main", "days_ago": 0.1,
                 "role_description": "core dev"},
                {"project": "nimbus", "instance": "frames-team", "days_ago": 0.5,
                 "role_description": "frame app"},
                {"project": "claudecontrol", "instance": "claude-control", "days_ago": 0.0,
                 "role_description": "this UI"},
                {"project": "ha", "instance": "sage", "days_ago": 5.0,
                 "role_description": "HA manager"},
            ]
            proj_filter = kwargs.get("project")
            if proj_filter:
                all_agents = [a for a in all_agents if a["project"] == proj_filter]
            payload = {"agents": all_agents}
        elif tool == "memory_get_messages":
            agent = kwargs.get("for_instance", "?")
            payload = {
                "messages": [
                    {
                        "id": "msg_demo_1",
                        "from": "tom",
                        "from_project": "claudecontrol",
                        "category": "info",
                        "message": f"hello {agent}, this is a stub seed message.",
                        "priority": "normal",
                        "status": "delivered",
                        "created": "2026-04-29T15:00:00Z",
                        "sent_by_human": True,
                        "require_human": False,
                    }
                ]
            }
        elif tool == "memory_heartbeat":
            payload = {"ok": True}
        elif tool == "memory_list_backlog":
            payload = {
                "count": 2, "total": 2,
                "items": [
                    {"id": "backlog_demo_1", "title": "Demo backlog item",
                     "status": "open", "priority": "high", "project": "claudecontrol",
                     "assigned_to": "claude-control", "tags": ["demo"]},
                    {"id": "backlog_demo_2", "title": "Another item",
                     "status": "in_progress", "priority": "medium", "project": "nimbus",
                     "assigned_to": "main", "tags": []},
                ],
            }
        elif tool == "memory_list_specs":
            payload = {
                "specs": [
                    {"name": "claudeControl:message_api", "version": "1.0.0",
                     "project": "shared_memory", "spec_type": "interface", "owner": "shared-memory"},
                    {"name": "demo:thing", "version": "0.1.0",
                     "project": "nimbus", "spec_type": "schema", "owner": "main"},
                ]
            }
        elif tool == "memory_get_spec":
            payload = {
                "spec_name": kwargs.get("name", "?"),
                "version": "1.0.0",
                "owner": "stub",
                "spec_type": "interface",
                "content": "# stub spec\n\nThis is stub spec content for dev rendering.",
                "tags": ["stub"],
                "created": "2026-04-29T00:00:00Z",
                "updated": "2026-04-29T00:00:00Z",
            }
        elif tool == "memory_send_message":
            body = kwargs.get("message", "")
            destructive = bool(__import__("re").search(
                r"\b(DELETE|DROP|TRUNCATE|deploy|production|prod\b)\b"
                r"|git\s+push\s+(--force|-f)\b",
                body,
                __import__("re").IGNORECASE,
            ))
            payload = {
                "status": "queued",
                "message_id": "msg_stub_" + str(abs(hash(body)) % 10**8),
                "to": kwargs.get("to_instance"),
                "to_project": kwargs.get("to_project"),
                "destructive_match": destructive,
                "require_human": destructive,
                "persisted": True,
            }
        else:
            payload = {}
        text = json.dumps({"result": json.dumps(payload)})
        return SimpleNamespace(content=[SimpleNamespace(text=text)])


def main() -> None:
    settings = Settings(
        mcp_url="stub",
        tom_web_api_key="stub",
        agent_name="user",
        project="claudecontrol",
        session_secret="stub-session-secret-for-dev-only-do-not-use" * 2,
        login_passphrase="dev",
        host="127.0.0.1",
        port=8765,
        log_level="INFO",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = StubMCP()
        broker = InboxBroker(client)  # type: ignore[arg-type]
        app.state.mcp = client
        app.state.broker = broker
        app.state.settings = settings
        # Pre-watch a couple projects for demo.
        await broker.watch_project("nimbus")
        try:
            yield
        finally:
            await broker.stop_all()

    app = FastAPI(title="junto-control-dev", lifespan=lifespan)
    store = SessionStore(settings.session_secret)
    app.include_router(build_router(store, settings.login_passphrase))

    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    main()
