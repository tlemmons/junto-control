# claudeControl

Web UI for messaging Claude agents via the shared-memory MCP server.

## What this is

A FastAPI backend + HTMX frontend that lets a human (Tom) log in, pick a project,
and exchange messages with the Claude agents running across all of the user's
projects. The contract this implements is `claudeControl:message_api` v1.0.0,
owned by the `shared-memory` agent.

## Quick start (dev)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

cp .env.example .env
# fill TOM_WEB_API_KEY, SESSION_SECRET, MCP_URL

python -m claudecontrol.main
# visit http://localhost:8000/healthz
```

## Configuration

| Var | Required | Notes |
|---|---|---|
| `MCP_URL` | yes | shared-memory MCP server, e.g. `http://localhost:8080/mcp` |
| `TOM_WEB_API_KEY` | yes | user-tier API key. Server-injected; never log or commit |
| `SESSION_SECRET` | yes | signs browser session cookies |
| `CLAUDECONTROL_AGENT_NAME` | no | default `claude-control` |
| `CLAUDECONTROL_PROJECT` | no | default `claudecontrol` (lowercase, canonical) |
| `HOST` / `PORT` | no | default `0.0.0.0:8000` |
| `LOG_LEVEL` | no | default `INFO` |

## Self-hosting requirements

The MCP server backing the UI must implement `claudeControl:message_api` v1.0.0.
Hard requirements (UI refuses to start if missing):

- `memory_start_session`
- `memory_send_message`
- `memory_get_messages`
- `memory_acknowledge_message`
- `memory_list_agents`
- `memory_get_spec`
- `memory_list_backlog`

Soft requirements (graceful-degrade if missing):

- Inbox `resources/subscribe` (`inbox://...`) — falls back to polling on absence.
- cterm-inbox plugin on recipient harness — without it, agents pull messages
  manually rather than getting them pushed.
- Audit log integration — UI logs locally if absent.

The UI's footer surfaces detected feature availability ("Live updates: enabled" /
"polling fallback") so you can see the degrade live.

## Architecture

- **Single MCP session per backend process**, opened with the `tom-web` user-tier
  key at startup. user-tier role is `projects: "all"` so one session reads/writes
  any project.
- **Browser ↔ FastAPI**: signed-cookie session, no MCP session_id ever crosses
  the wire to the browser.
- **Live updates**: WebSocket fan-out from a per-process broker that subscribes
  to `inbox://<project>/<agent>` URIs for the agents in the user's currently
  selected project. Project switcher rotates the subscription set.
- **No direct MongoDB access**. All reads/writes go through MCP tools per
  contract rule 5.

## Development

```bash
uv pip install -e ".[dev]"
ruff check src tests
mypy src
pytest
```

## Project naming convention

- Project name in MCP calls: `claudecontrol` (lowercase — canonical form)
- Spec name: `claudeControl:message_api` (camelCase verbatim)
- Agent name: `claude-control`
- Display / directory: `claudeControl`
