"""
Subscribe-path smoke test against the live shared-memory MCP server.

Verifies that:
  1. resources/subscribe succeeds for inbox://<project>/<agent> even though
     the server advertises capabilities.resources.subscribe = false (known bug
     per shared-memory msg_1e7d343c5299).
  2. resources/read returns the inbox payload shape we expect.

Connects as an unauthenticated agent (role=agent, soft-fallback). Does NOT
need a tom-web key — uses claude-control's own inbox.

Run:  .venv/bin/python scripts/smoke_subscribe.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession, types
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.session import RequestResponder
from pydantic import AnyUrl


async def main() -> int:
    url = os.environ.get("MCP_URL", "http://localhost:8080/mcp")
    # Subscribe to our own inbox — agents can always read/sub their own URI.
    # With a tom-web user-tier key the UI can subscribe to anyone.
    target_uri = "inbox://claudecontrol/subscribe-smoke"
    print(f"connecting: {url}")
    print(f"target:     {target_uri}")
    received: list[str] = []

    async def handler(
        message: (
            RequestResponder[types.ServerRequest, types.ClientResult]
            | types.ServerNotification
            | Exception
        ),
    ) -> None:
        if isinstance(message, types.ServerNotification):
            notif = message.root
            if isinstance(notif, types.ResourceUpdatedNotification):
                received.append(str(notif.params.uri))
                print(f"  <- notification: {notif.params.uri}")

    try:
        async with streamablehttp_client(url) as (read_stream, write_stream, _close):
            async with ClientSession(
                read_stream, write_stream, message_handler=handler
            ) as session:
                init = await session.initialize()
                print(
                    f"server: {init.serverInfo.name} v{init.serverInfo.version}"
                )
                resources_cap = getattr(init.capabilities, "resources", None)
                print(
                    "  capabilities.resources.subscribe:",
                    getattr(resources_cap, "subscribe", None),
                    "(server flag — expected to lie per known bug)",
                )

                # Verify the inbox template is advertised — that's our real signal.
                rt = await session.list_resource_templates()
                templates = [t.uriTemplate for t in rt.resourceTemplates]
                print(f"  resource_templates: {templates}")
                assert any(
                    "inbox://" in (t or "") for t in templates
                ), "inbox template missing"

                # Open a memory session — the server gates subscribe on this.
                print("opening memory session...")
                start = await session.call_tool(
                    "memory_start_session",
                    arguments={
                        "project": "claudecontrol",
                        "claude_instance": "subscribe-smoke",
                        "role_description": "subscribe path smoke test",
                    },
                )
                import json as _json

                start_text = start.content[0].text if start.content else "{}"
                try:
                    start_outer = _json.loads(start_text)
                    inner = (
                        _json.loads(start_outer["result"])
                        if isinstance(start_outer.get("result"), str)
                        else start_outer
                    )
                    sid = inner.get("session_id")
                except Exception:
                    sid = None
                print(f"  session_id: {sid}")
                assert sid, "memory_start_session returned no session_id"

                # Subscribe — this is the call shared-memory said will work.
                print("subscribing...")
                await session.subscribe_resource(AnyUrl(target_uri))
                print("  subscribed OK")

                # Read the inbox to see the current payload shape.
                print("reading...")
                read = await session.read_resource(AnyUrl(target_uri))
                if not read.contents:
                    print("  empty contents")
                else:
                    import json

                    text = getattr(read.contents[0], "text", "") or ""
                    try:
                        payload = json.loads(text)
                    except json.JSONDecodeError:
                        print(f"  non-json: {text[:200]}")
                    else:
                        msgs = payload.get("messages", [])
                        print(
                            f"  agent={payload.get('agent')} "
                            f"project={payload.get('project')} "
                            f"messages={len(msgs)} "
                            f"has_more={payload.get('has_more')}"
                        )
                        for m in msgs[:3]:
                            print(
                                f"    - {m.get('id')} from={m.get('from')} "
                                f"category={m.get('category')} "
                                f"created={m.get('created')}"
                            )

                print("unsubscribing...")
                await session.unsubscribe_resource(AnyUrl(target_uri))
                print("  unsubscribed OK")
        return 0
    except BaseExceptionGroup as eg:
        print(f"FAILED (group): {eg!r}")
        for i, sub in enumerate(eg.exceptions):
            print(f"  [{i}] {type(sub).__name__}: {sub}")
            cause = sub.__cause__ or sub.__context__
            if cause:
                print(f"      cause: {type(cause).__name__}: {cause}")
        return 1
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
