"""
Standalone transport smoke test.

Connects to the MCP server, initializes, lists tools and resource templates,
and prints the server's advertised capabilities — WITHOUT calling
memory_start_session. Useful for validating the MCP_URL + transport wiring
even when you don't have a tom-web key at hand.

Run:  .venv/bin/python scripts/smoke_mcp.py
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client


async def main() -> int:
    url = os.environ.get("MCP_URL", "http://localhost:8080/mcp")
    print(f"connecting: {url}")
    try:
        async with streamablehttp_client(url) as (read_stream, write_stream, _close):
            async with ClientSession(read_stream, write_stream) as session:
                init = await session.initialize()
                print(f"server: {init.serverInfo.name} v{init.serverInfo.version}")
                resources_cap = getattr(init.capabilities, "resources", None)
                print(
                    "capabilities.resources.subscribe:",
                    getattr(resources_cap, "subscribe", None),
                )
                tools = await session.list_tools()
                print(f"tools: {len(tools.tools)}")
                memory_tools = sorted(t.name for t in tools.tools if t.name.startswith("memory_"))
                print(f"  memory_*: {len(memory_tools)}")
                for name in memory_tools[:8]:
                    print(f"    - {name}")
                if len(memory_tools) > 8:
                    print(f"    ... and {len(memory_tools) - 8} more")
                try:
                    rt = await session.list_resource_templates()
                    print(f"resource_templates: {len(rt.resourceTemplates)}")
                    for t in rt.resourceTemplates:
                        print(f"  - {t.uriTemplate}  ({t.name})")
                except Exception as exc:
                    print(f"list_resource_templates failed: {exc}")
        return 0
    except Exception as exc:
        print(f"FAILED: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
