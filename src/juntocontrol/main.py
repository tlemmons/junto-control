from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from .auth import SessionStore
from .config import Settings
from .inbox import InboxBroker, InboxKey
from .mcp_client import MCPClient
from .web import build_router


def _configure_logging(level: str) -> None:
    logging.basicConfig(level=level)
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level, logging.INFO)
        ),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    _configure_logging(settings.log_level)
    log = structlog.get_logger("juntocontrol")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = MCPClient(settings)
        self_inbox = InboxKey(project=settings.project, agent=settings.agent_name)
        broker = InboxBroker(client, self_inbox=self_inbox)
        client.set_notification_handler(broker.on_inbox_notification)
        client.register_reconnect_handler(broker.on_reconnect)
        app.state.mcp = client
        app.state.broker = broker
        app.state.settings = settings
        log.info("startup_begin", mcp_url=settings.mcp_url, project=settings.project)
        await client.start()
        await broker.ensure_always_watched()
        log.info("startup_done", connected=client.connected, session_id=client.session_id)
        try:
            yield
        finally:
            log.info("shutdown_begin")
            await broker.stop_all()
            await client.stop()
            log.info("shutdown_done")

    app = FastAPI(title="junto-control", version="0.1.0", lifespan=lifespan)
    store = SessionStore(settings.session_secret)
    app.include_router(build_router(store, settings.login_passphrase))

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        client: MCPClient = app.state.mcp
        broker: InboxBroker = app.state.broker
        caps = client.capabilities
        return {
            "ok": client.connected,
            "session_id": client.session_id,
            "last_connected_at": client.last_connected_at,
            "capabilities": {
                "tools": sorted(caps.tools),
                "resources_subscribe": caps.resources_subscribe,
                "inbox_resource_supported": caps.inbox_resource_supported,
                "missing_required_tools": sorted(caps.missing_required()),
            },
            "watched_streams": [str(k) for k in broker.watched_keys],
        }

    @app.post("/api/project/select")
    async def select_project(payload: dict[str, str]) -> dict[str, Any]:
        broker: InboxBroker = app.state.broker
        project = (payload.get("project") or "").strip().lower()
        if not project:
            return JSONResponse({"error": "project required"}, status_code=400)
        keys = await broker.watch_project(project)
        return {"project": project, "watching": [k.agent for k in keys]}

    @app.websocket("/ws/inbox")
    async def ws_inbox(websocket: WebSocket) -> None:
        broker: InboxBroker = app.state.broker
        project = websocket.query_params.get("project") or None
        await websocket.accept()
        sub = broker.subscribe(project_filter=project)
        try:
            while not sub.closed:
                event = await sub.queue.get()
                await websocket.send_json(
                    {
                        "type": "inbox.message",
                        "project": event.key.project,
                        "agent": event.key.agent,
                        "message": event.message,
                        "received_at": event.received_at,
                    }
                )
        except WebSocketDisconnect:
            pass
        except asyncio.CancelledError:
            raise
        finally:
            broker.unsubscribe(sub)

    return app


app = create_app() if __name__ == "__main__" else None  # type: ignore[assignment]


def run() -> None:
    import uvicorn

    settings = Settings.from_env()
    uvicorn.run(
        "juntocontrol.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=False,
    )


if __name__ == "__main__":
    run()
