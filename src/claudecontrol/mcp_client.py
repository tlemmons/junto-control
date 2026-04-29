from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field
from typing import Any

import structlog
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .config import Settings

log = structlog.get_logger(__name__)

REQUIRED_TOOLS: tuple[str, ...] = (
    "memory_start_session",
    "memory_send_message",
    "memory_get_messages",
    "memory_acknowledge_message",
    "memory_list_agents",
    "memory_get_spec",
    "memory_list_backlog",
)

HEARTBEAT_INTERVAL_SEC = 300
RECONNECT_BASE_SEC = 1.0
RECONNECT_MAX_SEC = 30.0


@dataclass
class Capabilities:
    tools: set[str] = field(default_factory=set)
    resources_subscribe: bool = False
    inbox_resource_supported: bool = False

    def missing_required(self) -> set[str]:
        return set(REQUIRED_TOOLS) - self.tools


class MCPClient:
    """
    Single persistent MCP session for the entire UI backend process.
    Holds the user-tier session_id, restarts on transport drops, exposes
    typed tool-call helpers.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._session: ClientSession | None = None
        self._session_id: str | None = None
        self._capabilities: Capabilities = Capabilities()
        self._stack: contextlib.AsyncExitStack | None = None
        self._lock = asyncio.Lock()
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()
        self._stopping = False
        self._last_connected_at: float | None = None

    @property
    def session_id(self) -> str | None:
        return self._session_id

    @property
    def capabilities(self) -> Capabilities:
        return self._capabilities

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    @property
    def last_connected_at(self) -> float | None:
        return self._last_connected_at

    async def start(self) -> None:
        """Connect with exponential backoff, kick off heartbeat task."""
        backoff = RECONNECT_BASE_SEC
        while not self._stopping:
            try:
                await self._connect()
                backoff = RECONNECT_BASE_SEC
                break
            except Exception as exc:
                log.warning("mcp_connect_failed", error=str(exc), backoff_sec=backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, RECONNECT_MAX_SEC)
        if not self._stopping and self._heartbeat_task is None:
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        self._stopping = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
            self._heartbeat_task = None
        await self._teardown()

    async def call(self, tool: str, **arguments: Any) -> Any:
        """Invoke an MCP tool, auto-injecting session_id when applicable."""
        if not self._session or not self._connected.is_set():
            raise RuntimeError("mcp session not connected")
        # All shared-memory tools that require a session take session_id as a param.
        if "session_id" not in arguments and self._session_id and tool != "memory_start_session":
            arguments["session_id"] = self._session_id
        result = await self._session.call_tool(tool, arguments=arguments)
        return result

    async def _connect(self) -> None:
        async with self._lock:
            await self._teardown()
            stack = contextlib.AsyncExitStack()
            try:
                read_stream, write_stream, _close = await stack.enter_async_context(
                    streamablehttp_client(self._settings.mcp_url)
                )
                session = await stack.enter_async_context(
                    ClientSession(read_stream, write_stream)
                )
                init_result = await session.initialize()
                tools_result = await session.list_tools()
                tool_names = {t.name for t in tools_result.tools}
                resources_cap = getattr(init_result.capabilities, "resources", None)
                resources_subscribe = bool(getattr(resources_cap, "subscribe", False))
                # Probe whether inbox:// resource templates are advertised.
                inbox_supported = False
                try:
                    rt = await session.list_resource_templates()
                    inbox_supported = any(
                        "inbox://" in (t.uriTemplate or "") for t in rt.resourceTemplates
                    )
                except Exception as exc:
                    log.debug("list_resource_templates_failed", error=str(exc))
                self._capabilities = Capabilities(
                    tools=tool_names,
                    resources_subscribe=resources_subscribe,
                    inbox_resource_supported=inbox_supported,
                )
                missing = self._capabilities.missing_required()
                if missing:
                    raise RuntimeError(
                        f"MCP server missing required tools: {sorted(missing)}"
                    )

                # Open the user-tier shared-memory session.
                start = await session.call_tool(
                    "memory_start_session",
                    arguments={
                        "api_key": self._settings.tom_web_api_key,
                        "project": self._settings.project,
                        "claude_instance": self._settings.agent_name,
                        "role_description": (
                            "Human operator via claudeControl web UI"
                        ),
                    },
                )
                payload = _unwrap_tool_result(start)
                session_id = payload.get("session_id")
                if not session_id:
                    raise RuntimeError(f"memory_start_session returned no session_id: {payload}")

                self._session = session
                self._session_id = session_id
                self._stack = stack
                self._last_connected_at = time.time()
                self._connected.set()
                log.info(
                    "mcp_connected",
                    session_id=session_id,
                    tools=len(tool_names),
                    resources_subscribe=resources_subscribe,
                    inbox_resource_supported=inbox_supported,
                )
            except Exception:
                await stack.aclose()
                raise

    async def _teardown(self) -> None:
        self._connected.clear()
        if self._session and self._session_id:
            with contextlib.suppress(Exception):
                await self._session.call_tool(
                    "memory_end_session",
                    arguments={
                        "session_id": self._session_id,
                        "summary": "claudeControl backend shutdown / reconnect",
                    },
                )
        self._session = None
        self._session_id = None
        if self._stack is not None:
            with contextlib.suppress(Exception):
                await self._stack.aclose()
            self._stack = None

    async def _heartbeat_loop(self) -> None:
        while not self._stopping:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_SEC)
                if not self._connected.is_set():
                    continue
                with contextlib.suppress(Exception):
                    await self.call("memory_heartbeat")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("heartbeat_error", error=str(exc))
                self._connected.clear()
                await self.start()


def _unwrap_tool_result(result: Any) -> dict[str, Any]:
    """
    shared-memory tools wrap their JSON payload in a single TextContent block
    whose .text is JSON. Sometimes it's nested under {"result": "<json>"}.
    """
    import json

    if not getattr(result, "content", None):
        return {}
    text = result.content[0].text
    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    if isinstance(outer, dict) and isinstance(outer.get("result"), str):
        with contextlib.suppress(json.JSONDecodeError):
            return json.loads(outer["result"])
    return outer if isinstance(outer, dict) else {"value": outer}
