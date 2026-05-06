# junto-control

Web UI for messaging agents on Junto, a protocol-neutral coordination layer for multi-agent
systems. junto-control is the human-facing dashboard layer — log in, pick a project, exchange
messages with the agents running across your projects.

MIT licensed. Single-user mode in v0.1; multi-tenant in scope for later.

## Junto stack

- **[junto-stack](https://github.com/tlemmons/junto-stack)** — docker-compose bootstrap
  (chromadb + mongo + memory server). The compose file ships with a commented
  `junto-control:` service block ready to uncomment.
- **[junto-memory](https://github.com/tlemmons/junto-memory)** — the shared-memory MCP
  server this UI connects to.
- **junto-control** (this repo) — the human dashboard.

## What this is

A FastAPI backend + HTMX frontend that connects to a Junto-compatible MCP server (any
implementation of `claudeControl:message_api` v1.0.0 — that spec is the contract whether the
sender is Claude or another agent). Live updates use the MCP `inbox://<project>/<agent>`
resource subscription with a polling fallback.

## Quick start (dev)

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv venv
source .venv/bin/activate
uv pip install -e ".[dev]"

cp .env.example .env
# fill TOM_WEB_API_KEY (user-tier MCP key), SESSION_SECRET, LOGIN_PASSPHRASE, MCP_URL

python -m juntocontrol.main
# visit http://localhost:8000
```

## Configuration

| Var | Required | Notes |
|---|---|---|
| `MCP_URL` | yes | shared-memory MCP server, e.g. `http://localhost:8080/mcp` |
| `TOM_WEB_API_KEY` | yes | user-tier API key. Server-injected; never log or commit |
| `SESSION_SECRET` | yes | signs browser session cookies |
| `LOGIN_PASSPHRASE` | yes | passphrase for the single-user `/login` form |
| `JUNTOCONTROL_AGENT_NAME` | no | default `claude-control` (legacy MCP identity, kept for data continuity) |
| `JUNTOCONTROL_PROJECT` | no | default `claudecontrol` (legacy MCP project bucket) |
| `HOST` / `PORT` | no | default `0.0.0.0:8000` |
| `LOG_LEVEL` | no | default `INFO` |

The `claude-control` / `claudecontrol` defaults are intentional — the package was renamed
from `claudecontrol` to `juntocontrol` but the MCP identity values are kept so the project's
existing state spec, registered functions, message threads, and learnings under those
identifiers stay reachable. New deployments can override these freely.

## Self-hosting requirements

The MCP server backing the UI must implement `claudeControl:message_api` v1.0.0. Hard
requirements (UI refuses to start if missing):

- `memory_start_session`
- `memory_send_message`
- `memory_get_messages`
- `memory_acknowledge_message`
- `memory_list_agents`
- `memory_get_spec`
- `memory_list_backlog`

Soft requirements (graceful-degrade if missing):

- Inbox `resources/subscribe` (`inbox://...`) — falls back to polling if not advertised.
- cterm-inbox plugin (or equivalent) on recipient harnesses — without it, agents pull
  messages manually rather than being live-pushed.
- Audit log integration — UI logs locally if absent.

The UI footer surfaces detected feature availability so you can see the degrade live.

## Architecture

- **Single MCP session per backend process**, opened with a user-tier key at startup. The
  user-tier role is `projects: "all"` so one session reads/writes any project.
- **Browser ↔ FastAPI**: signed-cookie session, no MCP `session_id` ever crosses the wire to
  the browser.
- **Live updates**: WebSocket fan-out from a per-process broker that subscribes to
  `inbox://<project>/<agent>` URIs for the agents in the user's currently selected project.
  Project switcher rotates the subscription set.
- **No direct MongoDB or Chroma access**. All reads/writes go through MCP tools per
  contract rule 5.

## Development

```bash
uv pip install -e ".[dev]"
ruff check src tests
mypy src
pytest
```

A live broker smoke test is provided:

```bash
.venv/bin/python scripts/smoke_broker.py   # requires MCP_URL pointing at a live server
```

## License

MIT — see [LICENSE](./LICENSE).
