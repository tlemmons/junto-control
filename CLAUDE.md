# claudeControl — web app for talking to Claude agents

## Claude Identity (REQUIRED — DO THIS FIRST)

**Your name is: `claude-control`**

**IMMEDIATELY on session start, run these commands IN ORDER:**

1. Rename this session:
```
/rename claude-control
```

2. Set terminal title:
```bash
echo -ne "\033]0;[claude-control] claudeControl\007"
```

3. Start shared memory session (NO api_key — you connect as agent, not user).
   **CRITICAL: project name is lowercase `claudecontrol`** — the server's project
   registry stores it lowercase, and there's a known case-sensitivity bug
   (backlog_a74d0f6a7f38) that breaks inbox filtering if the session uses a
   different casing than the messages do. Always pass project in lowercase.

```python
memory_start_session(
    project="claudecontrol",
    claude_instance="claude-control",
    role_description="Web UI backend that lets a human (Tom) send/receive messages with Claude agents via the shared-memory MCP server"
)
memory_list_backlog(project="claudecontrol", assigned_to="claude-control")
memory_get_messages()
```

4. Read the interface contract you must follow:
```python
memory_get_spec(name="claudeControl:message_api", project="shared_memory")
```

That spec is OWNED BY `shared-memory` and is the source of truth for what
tools you call, payload shapes, auth flow, and feature priority. If you
think it needs amending, send a question via memory_send_message to
to_instance="shared-memory", to_project="shared_memory" — do not
freelance the contract.

### Naming convention reminder
- **Project name** in MCP calls: `claudecontrol` (lowercase, canonical form)
- **Spec name**: `claudeControl:message_api` (camelCase — spec names are stored
  verbatim, not normalized)
- **Agent name**: `claude-control` (lowercase with hyphen)
- **Directory / display**: `claudeControl` (camelCase, human-friendly)

When in doubt for the `project=` parameter, use lowercase.

## What This Project Is

claudeControl is a web app where a human logs in and sends/receives
messages with the Claude agents running across all the user's projects
(shared_memory, claude_terminal, emailtriage, nimbus, etc). The UI
service itself uses the `tom-web` user-tier API key at runtime — but
YOU (the Claude building this UI) are an agent, NOT a human, and you
do NOT use `tom-web` for your own MCP calls.

## Auth model — two layers
- **You (developer-Claude)**: connect to MCP with no api_key → role=agent
- **The UI service you build**: uses `tom-web` at runtime → role=user

The `tom-web` key value lives in the shared-memory server's
`db.api_keys` collection. Tom will deliver it via env var to the
deployed UI service. Do NOT hardcode it. Do NOT log it.

## Stack & infra
- Open question: pick a stack and propose it (FastAPI + HTMX? Node +
  React?). The MCP Python SDK is most mature, so a Python backend is
  the path of least resistance unless there's a strong reason to go
  elsewhere.
- The shared-memory MCP server lives at `http://localhost:8080/mcp`
  on `sage`. The UI backend connects to it as an MCP client.
- Mongo + Chroma are inside the MCP server's container — DO NOT
  connect to them directly. All reads/writes go through MCP tools
  (rule 5 of the interface spec).

## Scope
Full development access to all files in this folder. Coordinate
cross-project changes via memory_send_message — the server-side owner
of the messaging contract is `shared-memory`.
