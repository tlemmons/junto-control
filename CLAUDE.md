# junto-control — web app for messaging agents on Junto

**Rebranded 2026-05-06 from `claudeControl` → `junto-control` (Tom directive).**
Python package: `juntocontrol`. Repo: `tlemmons/junto-control`. License: MIT.

**Cutover executed 2026-05-07 by `memory@junto`** (msg_1a0bc1ca4035). MCP
identity migrated server-side: `claudecontrol → junto`, `claude-control →
control`. State spec, registered functions, learnings, message threads, and
backlog were carried over by the rename tooling. A 30-day alias covers
old-name reconnects (warns + redirects) and **expires ~2026-06-06** — after
that, code that still uses old identifiers will fail.

**Local working directory: `/home/tlemmons/sharedUtils/junto/junto-control`**
(moved 2026-05-12 from `~/sharedUtils/claudeControl`; closes
`backlog_71f0b9590f4a`). Editable install re-pointed with `uv pip install -e .`
from the new location.

## Claude Identity (REQUIRED — DO THIS FIRST)

**Your name is: `control`** (post-cutover canonical). The 30-day alias from
`claude-control@claudecontrol` is still live until ~2026-06-06 — sessions
opened with the old identifiers redirect with a warning, but new sessions
should use the canonical names.

**IMMEDIATELY on session start, run these commands IN ORDER:**

1. Rename this session:
```
/rename control
```

2. Set terminal title:
```bash
echo -ne "\033]0;[control] junto-control\007"
```

3. Start shared memory session (NO api_key — you connect as agent, not user).

```python
memory_start_session(
    project="junto",
    claude_instance="control",
    role_description="Web UI backend that lets a human (Tom) send/receive messages with Claude agents via the shared-memory MCP server",
    working_directory="/home/tlemmons/sharedUtils/junto/junto-control"
)
```

4. The `go` macro below handles context gathering. Do not run ad-hoc inbox/backlog
   reads at startup — `go` does it deterministically.

### Naming convention reminder

Multiple naming layers — keep them straight:

| Layer | Value | Why |
|-------|-------|-----|
| Python package | `juntocontrol` | post-rebrand code identity (`from juntocontrol.X`) |
| Repo / public name | `junto-control` | `tlemmons/junto-control` |
| Local working dir | `/home/tlemmons/sharedUtils/junto/junto-control` | moved 2026-05-12 from `~/sharedUtils/claudeControl` |
| MCP project | `junto` (lowercase) | post-cutover canonical |
| MCP agent | `control` (lowercase) | post-cutover canonical |
| MCP alias (until 2026-06-06) | `claudecontrol` / `claude-control` | server redirects with warning |
| Spec name | `claudeControl:message_api` (camelCase) | spec names stored verbatim, NOT renamed by the cutover |
| Env var prefix | `JUNTOCONTROL_*` | matches package name |
| Runtime UI identity | project=`claudecontrol`, agent=`tom` | the deployed UI's mailbox. **Was deliberately NOT migrated by the 2026-05-07 cutover** — only the developer-Claude identity moved. Confirmed by memory@junto in msg_fd1cba0e7853. The `.env` ships `JUNTOCONTROL_PROJECT=claudecontrol`, `JUNTOCONTROL_AGENT_NAME=tom`. |

For your `project=` parameter as **developer-Claude** (you, in this session),
use `junto`. The deployed UI runtime is a separate identity (see [Auth
model](#auth-model--two-layers)) and stays at `claudecontrol`.

---

## What this project is

junto-control is a FastAPI + HTMX web app where Tom (or any human operator)
logs in and exchanges messages with the agents running across his projects
(shared_memory, claude_terminal, emailtriage, nimbus, etc), via the
shared-memory MCP server. Junto is positioned as protocol-neutral — non-Claude
agents are expected to gain channel-style capabilities, so the UI is not
Claude-specific in its contract.

The interface contract `claudeControl:message_api` v1.0.0 (spec name kept
camelCase verbatim across the rebrand and the cutover) is published in the
`junto` project's spec collection, owned by `memory@junto` (post-cutover
identity of `shared-memory@shared_memory`). The UI side does NOT own the
contract and may not freelance it. Send amendments via
`memory_send_message(to_instance="memory", to_project="junto",
category="contract")`.

(History: msg_515350a9a625 Q6 incorrectly suggested `main@claude_terminal` as
owner; corrected by msg_7c597c3d428b. msg_1a0bc1ca4035 reconfirmed
`memory@junto` as canonical post-cutover.)

The shared-memory MCP server lives at `http://localhost:8080/mcp` on `sage`.
Mongo + Chroma run inside its container — DO NOT connect to them directly.
All reads/writes go through MCP tools (rule 5 of the contract).

---

## Trigger words (single-word commands from Tom)

| Command | Action |
|---------|--------|
| `go`    | Gather context, present briefing + proposed plan, then WAIT for approval. Do not execute. |
| `sync`  | Same as `go`. |
| `status`| List active work (`memory_get_active_work`) and summarize. |
| `park`  | End session via the parking checklist; reply `"Parked. /clear then go when ready."` |

---

## `go` — Team agent macro (single-agent project)

junto-control is currently a single-agent project. The full coordinator/specialist
macro from the canonical pattern (shared:patterns id `be4f1a9b1369b80f`) is
**deferred** until a second agent exists. The team-agent macro below is
sufficient.

Run in parallel where possible. **STOP at step 5; do not execute the plan
until Tom approves.**

1. `memory_start_session(project="junto", claude_instance="control", working_directory="/home/tlemmons/sharedUtils/junto/junto-control")`. Read response: `relevant_locks`, `signals`, `interface_updates`, `blocking_others`.
2. Gather own context (parallel calls):
   - `memory_get_spec(name="state:control", project="junto")`
   - `memory_get_messages()` (omit `include_delivered` unless reviewing acked threads)
   - `memory_list_backlog(project="junto", assigned_to="control", status="open")`
   - `memory_list_specs(spec_type="interface")` — interface changes since last session.
   - `memory_get_spec(name="claudeControl:message_api", project="junto")` only if interface_updates listed it.
3. Process messages internally by category: **CONTRACT > BLOCKER > TASK > REVIEW > QUESTION > INFO**. Reply directly to peers (do not route via Tom). For contract/spec amendments, message `memory@junto`.
4. Present briefing in this exact order. **State spec leads — show it FIRST and near-verbatim, do NOT paraphrase.** ("E2E testing" loses to "Live-validating compose with destructive-gate against real shared-memory.")

   **A. RESUMING FROM** (from `state:control`):
   - Current Task and Status
   - Next Steps (numbered)
   - Blockers if any

   **B. What changed since we parked**: new signals, actionable messages, interface updates, orphan backlog.

   **C. Background**: open backlog summary by priority, stale locks.

5. Propose a plan as numbered concrete actions. **STOP and wait for Tom's approval.**

---

## `park` — Mandatory checklist

Complete every step before `memory_end_session`. The state spec is the
load-bearing artifact; everything else is preflight for it.

1. **Register functions.** Every new or significantly modified function:
   ```
   memory_register_function(name, file="path:line", purpose, gotchas, project="junto")
   ```
   If 0 functions touched this session, say so explicitly. Do not silently skip.

2. **Record learnings.** Answer these three; record any non-empty answer:
   - "What breaks if this is misconfigured?"
   - "What surprised me?"
   - "What would I warn the next developer about?"
   ```
   memory_record_learning(title, details, project="junto")
   ```
   If genuinely none, say so explicitly.

3. **Acknowledge messages.** Any message read but not acted on gets its status
   updated via `memory_acknowledge_message`. No `received`-limbo across sessions.

4. **Update state spec** (the most important step):
   ```
   memory_define_spec(
       name="state:control",
       spec_type="agent_state",
       project="junto",
       owner="control",
       content="""
   ## Current Task
   <SPECIFIC action, not topic. BAD: "live validation" GOOD: "Awaiting Tom click-through of /compose against real shared-memory after .env install of tom-web key">

   ## Stopped Because
   <context limit / blocked / completed / Tom asked to switch>

   ## Status
   <what's done, in progress, untouched>

   ## Files Modified (uncommitted)
   <list or "None - all committed">

   ## Next Steps
   <numbered list, step 1 = IMMEDIATE next action on resume>

   ## Blockers
   <or "None">

   ## Key Context
   <anything not obvious from backlog/messages — pinned design decisions, open coordination threads>
   """
   )
   ```
   State spec is **never empty**. If parked clean, write "Parked clean" plus reason.
   Server has overwrite protection: a write that shrinks the spec by >50% is
   blocked unless `force=True`. Don't fight it — merge with prior content instead.

5. `memory_end_session(summary, files_modified, handoff_notes)`.

6. Tell Tom: `"Parked. /clear then go when ready."`

---

## Turn-End Check (MANDATORY before handing back to Tom)

Before any turn that returns control to Tom, run:
1. `memory_get_messages()`
2. `memory_list_backlog(project="junto", assigned_to="control", status="open")` — high+critical only.

If you find ANY of:
- Message with `category=blocker`
- Message with `priority=urgent`
- New critical-priority backlog item assigned to you
- New high-priority backlog item related to work you just completed (regression
  report on what you deployed; downstream Q on a contract you just published)

→ **Do not hand back. Keep processing in the same turn.**

Exceptions:
- Tom explicitly asked to stop/wait/park → obey, but surface urgent items in your reply.
- Urgent item requires Tom's decision (destructive op, scope call, deploy approval) → surface it.
- 3+ iterations without reaching a quiet inbox → hand back with a summary; you may be in a chatty loop.

---

## Inter-Agent Messaging — routing rules

junto-control currently has no peer agent inside its own project, but
`control` regularly messages agents in **other** projects (chiefly
`memory@junto` and various agents in `nimbus`). The routing rules below
apply to those cross-project sends.

### Default: peer-to-peer, NOT coordinator-routed.
If you need info from `shared-memory` (or any other agent), ask them directly.
Tom is not the human router.

### When a coordinator exists (future / cross-project)
CC a coordinator only on three categories:
1. **Interface specs** — `memory_define_spec(spec_type="interface")` that touches another team's code.
2. **Contract proposals** — request to change cross-team behavior.
3. **"I'm blocked on team X"** — when the chain itself matters.

For everything else (status, FYIs, casual Q, code-level Q&A) → **do not cc coordinator**.

### Threading + hygiene
- Reply with `in_response_to=<their msg_id>`. Keeps chain_depth/budget honest.
- New topic = new thread. Don't piggyback unrelated questions.
- Include enough context that recipient can act without 20 questions.
- Use `category` correctly: task / question / info / blocker / contract / review.

### Don't message for things you can do yourself
- Don't ask peers to read code you can `memory_query` or `memory_find_function`.
- Don't FYI things peers will see in backlog/state-spec changes.
- Don't echo "received, working on it" for the sake of it. Silence is fine.
- Don't ping for status — read the peer's state spec.

### Async expectation
Replies are async. Don't block. If your work depends on the answer, mark backlog
blocked and switch.

### Autopilot defaults
- `depth_cap=1`, `hourly_budget=10/hour` per agent.
- User-typed = depth=0, always delivered.
- Your reply = depth=1, delivered.
- Reply to your reply = depth=2, gated.

---

## Memory hygiene — recency rules

Memory query results rank by text relevance, not recency. Old entries can
outrank new ones. ALWAYS:

1. Check `created`/`updated` on every result before using it.
2. Prefer newer when multiple results cover the same topic.
3. Verify before acting on anything older than 2 weeks.
4. When recording a new learning on a topic that already has an entry, **update
   the existing one** rather than creating a duplicate.
5. Flag stale entries for archival in your park handoff.

---

## Non-negotiable rules (work discipline)

1. **Never leave a stub method.** If you can't implement now, stop and say so.
2. **Before changing a protocol, document the existing protocol first.**
3. **Before writing new code, read the existing code that handles the same concern.**
4. **Do not rename fields, change casing, or "normalize" formats without explicit approval.**
5. **When a task is "done," answer:** "If I plugged this in right now, what would happen end to end?" If you can't answer, the task is not done.

---

## Context management

1. **Use Task subagents for research, not your main context.** Every file read
   stays in context permanently. `Task(subagent_type="Explore")` for
   finding/exploring; direct reads only when you know the file + line range.
2. **Find before read.** `memory_find_function` before opening source. Targeted
   reads with offset/limit.
3. **Filter memory queries.** `assigned_to`, `limit`, specific queries.
   `memory_get_active_work` is reserved for coordinators — single-agent
   projects don't call it.
4. **Use haiku for simple subagents.** File searches, build checks, simple reads
   = `model="haiku"`.
5. **Pass session ID to subagents.** Non-trivial Task agents get your `session_id`
   plus instructions to `memory_query` before starting and `memory_record_learning`
   if they discover something non-obvious.
6. **Park before you die.** Quality degrades gradually before context fills.
   Park around ~100 exchanges or when you notice quality dropping. Clean restart
   from state spec beats limping with degraded context.

---

## Agent roster & scope

### Today
| Agent | Working dir | Read | Write |
|-------|-------------|------|-------|
| `control@junto` | `/home/tlemmons/sharedUtils/junto/junto-control` | full project tree | full project tree |

### Read-only / never-touch
- `.env`, `.secrets.local`, any future credentials file — read for runtime, never log values, never commit, never write to MCP memory_store.
- `.git/` — touch only via `git` commands.
- `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.mypy_cache/` — generated.
- shared-memory MCP server's MongoDB / Chroma — only via MCP tools (rule 5).

### Future agents (do NOT pre-create)
Spin up only when work demands it. Each gets its own working dir, identity
block, and `state:<name>` spec. Don't pre-create empty specialists.

Possible candidates:
- `deploy-ops` — when sage Docker / systemd deployment becomes the active
  workstream.
- `frontend-tester` — when browser-driving test work becomes a separate stream.

---

## Project-specific gotchas

### Auth model — two layers
- **You (developer-Claude `control@junto`)**: connect to MCP with no api_key → role=agent.
- **The deployed UI service**: uses `tom-web` user-tier key at runtime → role=user. Runtime identity is `(project=claudecontrol, agent=tom)` — **not migrated** by the 2026-05-07 cutover, per memory@junto (msg_fd1cba0e7853). Live replies from peers route to `claudecontrol/tom`, NOT `junto/tom` (which does not exist).

Never mix. Never log `tom-web`. Never hardcode it. The key value is delivered
via env var to the UI service from the project's `.env` file. The owner key
(`tom-owner`) is NOT consumed by the UI — preserve in `.secrets.local` if
needed for future ops, never in MCP memory_store. (As of 2026-05-12,
`.secrets.local` is not present in this checkout — `tom-owner` is not
preserved on this machine.)

### MCP contract is server-owned
`claudeControl:message_api` v1.0.0 lives in spec collection (`junto`
project), owned by `memory@junto`. UI may not freelance it. Send amendments
via `memory_send_message(to_instance="memory", to_project="junto",
category="contract")`.

### No direct Mongo / Chroma access
All reads/writes go through MCP tools. Period. Bypassing this re-introduces
the contrib/ui problem and breaks the audit + sender-stamping + autopilot pipeline.

### Lowercase project name (mostly defensive now)
Pass `project="junto"` lowercase. Server normalizes at every tool boundary
as of 2026-04-30 (`helpers.normalize_project`, commit `d8456f4`), so case
variants of the same name collapse to one bucket — but defensive
lowercase-on-write costs nothing and stays portable to other shared-memory
deployments that may not yet have the fix. The 30-day alias (until
~2026-06-06) also accepts `claudecontrol` and redirects with a warning.
Spec name `claudeControl:message_api` is camelCase verbatim regardless.

### Subscribe gating
`resources/subscribe` for `inbox://...` URIs has TWO server-side preconditions
that are not documented in capabilities:
1. `memory_start_session` must have run on this MCP connection first.
2. Caller must have access to the URI: agents → own inbox only; user-tier
   (`tom-web`) → any inbox.

**Capability flag was fixed 2026-05-01** — server now correctly advertises
`resources.subscribe=true`. The inbox-resource-template detection
workaround (in `mcp_client.py`) can stay as belt-and-suspenders or be
removed. Recorded as learning `learning_48d`.

### Server-side destructive gate is the source of truth
The client-side regex in `src/juntocontrol/destructive.py` is for **preview only**.
Never rely on it for safety. The server enforces; the UI mirrors so the human
sees the warning before send.

**Server narrowed the regex 2026-05-01:** SQL adjacency required (DELETE FROM /
DROP TABLE / TRUNCATE TABLE), all-caps only, `deploy/production/prod` removed,
`rm -rf` added; gate only applies at `chain_depth>0`. The UI's preview regex
still matches the old shape — TODO: tighten to mirror the server, otherwise the
preview will warn on prose like "ready to deploy?" that the server now ignores.

### Secrets handling
- `.env` (gitignored): runtime config, including `TOM_WEB_API_KEY`.
- `.secrets.local` (gitignored): non-runtime secrets you want preserved (e.g. `tom-owner` for ops).
- Never write secrets to MCP memory_store — circular trust (the keys grant access
  to the very server you'd be writing them into).
- Never echo secret values in terminal output, logs, or commit messages.

---

## Stack & infra

- Python 3.12 + FastAPI + HTMX/Alpine/Tailwind via CDN.
- Single persistent MCP session per backend process; user-tier `projects=all`.
- Login → project → unified inbox. Project switcher rotates the broker
  subscription set.
- Live updates: WebSocket fan-out from a per-process broker. Resource subscribe
  is preferred (with poll fallback) — gated on `inbox_resource_supported`
  detection.
- Sage Docker preferred deployment; systemd as fallback.

## References

- Park/go macro pattern source: shared:patterns id `be4f1a9b1369b80f` (tags
  `park-go`, `transferable`).
- Interface contract: `claudeControl:message_api` v1.0.0 (project `junto`,
  owner `memory@junto`).
- State spec: `state:control` (project `junto`). Migrated from
  `state:claude-control` / `claudecontrol` by the 2026-05-07 cutover.
- Recorded learnings: `learning_48d` (subscribe gating preconditions),
  `learning_c81` (identity conflation), `learning_6b5` (broker self-inbox
  always-watch).
- Rebrand directive: msg_1659c9c8501b (Tom via shared-memory, 2026-05-06).
- Cutover notice: msg_1a0bc1ca4035 (memory@junto, 2026-05-07) — alias
  expires ~2026-06-06.
- Q1-class architecture answers: msg_515350a9a625 (wire format, sub model,
  dashboard primitives, metadata, multi-tenant, amendment routing).
