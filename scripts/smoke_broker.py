"""
End-to-end live smoke for the subscribe consumer wire-up.

Validates the full path:
  server resources/updated notification
    -> ClientSession message_handler
    -> MCPClient-style notification handler
    -> InboxBroker.on_inbox_notification
    -> InboxBroker._fetch_via_resource (read_resource)
    -> Subscriber.queue.put(InboxEvent)

Connects as a fresh agent-tier session (no api_key needed; agent can sub its
own inbox), subscribes to its own URI, sends a message to itself, then asserts
the broker's subscriber queue gets the event with the matching message body.

Run:  .venv/bin/python scripts/smoke_broker.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from types import SimpleNamespace
from typing import Any

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.session import RequestResponder
from pydantic import AnyUrl

from claudecontrol.inbox import InboxBroker, InboxKey


class SessionShim:
    """Thin shim implementing the subset of MCPClient that InboxBroker uses."""

    def __init__(self, session: ClientSession) -> None:
        self._session = session
        self.capabilities = SimpleNamespace(inbox_resource_supported=True)
        self._notif_cb = None
        self._reconnect_cb = None

    def set_notification_handler(self, cb):
        self._notif_cb = cb

    def register_reconnect_handler(self, cb):
        self._reconnect_cb = cb

    async def fire_notification(self, uri: str) -> None:
        if self._notif_cb is not None:
            await self._notif_cb(uri)

    async def subscribe_inbox(self, uri: str) -> None:
        await self._session.subscribe_resource(AnyUrl(uri))

    async def unsubscribe_inbox(self, uri: str) -> None:
        try:
            await self._session.unsubscribe_resource(AnyUrl(uri))
        except Exception:
            pass

    async def read_resource(self, uri: str) -> dict[str, Any]:
        result = await self._session.read_resource(AnyUrl(uri))
        for content in result.contents:
            text = getattr(content, "text", None)
            if text is None:
                continue
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                continue
        return {}

    async def call(self, tool: str, **arguments: Any) -> Any:
        return await self._session.call_tool(tool, arguments=arguments)


def _unwrap(call_tool_result: Any) -> dict[str, Any]:
    if not getattr(call_tool_result, "content", None):
        return {}
    text = call_tool_result.content[0].text
    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        return {"raw": text}
    if isinstance(outer, dict) and isinstance(outer.get("result"), str):
        try:
            return json.loads(outer["result"])
        except json.JSONDecodeError:
            return outer
    return outer if isinstance(outer, dict) else {"value": outer}


async def main() -> int:
    url = os.environ.get("MCP_URL", "http://localhost:8080/mcp")
    agent = "broker-smoke"
    project = "claudecontrol"
    target_uri = f"inbox://{project}/{agent}"
    body = f"smoke probe {time.time():.2f}"

    print(f"connecting: {url}")
    print(f"target:     {target_uri}")

    shim_holder: dict[str, SessionShim] = {}

    async def message_handler(
        message: (
            RequestResponder[types.ServerRequest, types.ClientResult]
            | types.ServerNotification
            | Exception
        ),
    ) -> None:
        if not isinstance(message, types.ServerNotification):
            return
        notif = message.root
        if not isinstance(notif, types.ResourceUpdatedNotification):
            return
        uri = str(notif.params.uri)
        print(f"  <- notification: {uri}")
        shim = shim_holder.get("shim")
        if shim is not None:
            await shim.fire_notification(uri)

    try:
        async with streamablehttp_client(url) as (read_stream, write_stream, _close):
            async with ClientSession(
                read_stream, write_stream, message_handler=message_handler
            ) as session:
                init = await session.initialize()
                print(f"server: {init.serverInfo.name} v{init.serverInfo.version}")

                start = await session.call_tool(
                    "memory_start_session",
                    arguments={
                        "project": project,
                        "claude_instance": agent,
                        "role_description": "broker subscribe-consumer smoke",
                    },
                )
                start_payload = _unwrap(start)
                sid = start_payload.get("session_id")
                print(f"  session_id: {sid}")
                assert sid, "memory_start_session returned no session_id"

                shim = SessionShim(session)
                shim_holder["shim"] = shim
                broker = InboxBroker(shim)  # type: ignore[arg-type]
                shim.set_notification_handler(broker.on_inbox_notification)
                shim.register_reconnect_handler(broker.on_reconnect)

                # Bootstrap: ensure inbox is empty so we can detect the new send.
                # (Bootstrap path uses memory_get_messages via shim.call, which
                # works fine with agent-tier on own inbox.)
                sub = broker.subscribe()
                key = InboxKey(project, agent)
                await broker._start_stream(key)

                # Wait for the stream to enter subscribe mode.
                async with asyncio.timeout(5):
                    while broker._streams[key].mode != "subscribe":
                        await asyncio.sleep(0.05)
                print(f"  broker mode: subscribe (verified)")

                # Send a message to ourselves. Server should fire
                # notifications/resources/updated, the message_handler picks it
                # up, calls broker.on_inbox_notification, which fetches the
                # resource and pushes an InboxEvent onto sub.queue.
                print(f"sending self-message: {body!r}")
                send_result = await session.call_tool(
                    "memory_send_message",
                    arguments={
                        "session_id": sid,
                        "to_instance": agent,
                        "to_project": project,
                        "message": body,
                        "category": "info",
                        "priority": "normal",
                    },
                )
                send_payload = _unwrap(send_result)
                msg_id = send_payload.get("message_id")
                print(f"  send: message_id={msg_id} status={send_payload.get('status')}")

                # Wait for the broker subscriber to receive the event.
                print("waiting for broker event...")
                try:
                    async with asyncio.timeout(10):
                        event = await sub.queue.get()
                except TimeoutError:
                    print("  TIMEOUT — no broker event received in 10s")
                    await broker._stop_stream(key)
                    await broker.stop_all()
                    return 1

                received_body = event.message.get("message")
                received_id = event.message.get("id")
                print(
                    f"  EVENT key={event.key.uri()} "
                    f"msg_id={received_id} body={received_body!r}"
                )

                ok = received_body == body
                print(
                    "RESULT:",
                    "PASS — broker propagated the live notification"
                    if ok
                    else "FAIL — body mismatch",
                )

                # Cleanup.
                await broker._stop_stream(key)
                await broker.stop_all()
                with __import__("contextlib").suppress(Exception):
                    await session.call_tool(
                        "memory_end_session",
                        arguments={"session_id": sid, "summary": "broker smoke done"},
                    )

                return 0 if ok else 1
    except BaseExceptionGroup as eg:
        print(f"FAILED (group): {eg!r}")
        for i, sub_exc in enumerate(eg.exceptions):
            print(f"  [{i}] {type(sub_exc).__name__}: {sub_exc}")
            cause = sub_exc.__cause__ or sub_exc.__context__
            if cause:
                print(f"      cause: {type(cause).__name__}: {cause}")
        return 1
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
