from __future__ import annotations

import json
from collections import Counter
from importlib import resources
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from . import __version__
from .auth import (
    SessionStore,
    clear_cookie,
    constant_time_eq,
    issue_cookie,
)
from .destructive import matches_destructive
from .inbox import InboxBroker
from .mcp_client import MCPClient, _unwrap_tool_result

log = structlog.get_logger(__name__)


def _templates_dir() -> Path:
    return Path(resources.files("juntocontrol")) / "templates"  # type: ignore[arg-type]


def build_router(store: SessionStore, login_passphrase: str) -> APIRouter:
    router = APIRouter()
    templates = Jinja2Templates(directory=str(_templates_dir()))

    def render(request: Request, name: str, **ctx: Any) -> HTMLResponse:
        ctx.setdefault("version", __version__)
        return templates.TemplateResponse(request, name, ctx)

    @router.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request) -> HTMLResponse:
        if store.from_request(request).get("logged_in"):
            return RedirectResponse("/projects", status_code=303)  # type: ignore[return-value]
        return render(request, "login.html")

    @router.post("/login")
    async def login_submit(request: Request, passphrase: str = Form(...)) -> Any:
        if not constant_time_eq(passphrase, login_passphrase):
            log.info("login_failed", ip=request.client.host if request.client else "?")
            return render(request, "login.html", error="incorrect passphrase.")
        response = RedirectResponse("/projects", status_code=303)
        issue_cookie(response, store, {"logged_in": True, "project": None})
        return response

    @router.get("/logout")
    async def logout() -> Any:
        response = RedirectResponse("/login", status_code=303)
        clear_cookie(response)
        return response

    @router.get("/projects", response_class=HTMLResponse)
    async def projects_page(request: Request) -> Any:
        session = store.from_request(request)
        if not session.get("logged_in"):
            return RedirectResponse("/login", status_code=303)
        client: MCPClient = request.app.state.mcp
        agents_payload = _unwrap_tool_result(await client.call("memory_list_agents"))
        agents = list(agents_payload.get("agents", []))
        projects = _summarize_projects(agents)
        return render(request, "projects.html", projects=projects)

    @router.post("/projects/select")
    async def select_project(request: Request, project: str = Form(...)) -> Any:
        session = store.from_request(request)
        if not session.get("logged_in"):
            return RedirectResponse("/login", status_code=303)
        project = project.strip().lower()
        broker: InboxBroker = request.app.state.broker
        await broker.watch_project(project)
        response = RedirectResponse("/inbox", status_code=303)
        issue_cookie(response, store, {**session, "project": project})
        return response

    @router.get("/inbox", response_class=HTMLResponse)
    async def inbox_page(request: Request) -> Any:
        session = store.from_request(request)
        if not session.get("logged_in"):
            return RedirectResponse("/login", status_code=303)
        project = session.get("project")
        if not project:
            return RedirectResponse("/projects", status_code=303)
        client: MCPClient = request.app.state.mcp
        broker: InboxBroker = request.app.state.broker
        # Initial render: pull recent messages for every watched agent in project.
        seed = await _seed_inbox(client, broker, project)
        return render(request, "inbox.html", project=project, messages=seed)

    @router.get("/api/agents")
    async def api_agents(request: Request, project: str) -> Any:
        if not store.from_request(request).get("logged_in"):
            return JSONResponse({"error": "auth required"}, status_code=401)
        client: MCPClient = request.app.state.mcp
        result = await client.call("memory_list_agents", project=project.lower())
        agents = list(_unwrap_tool_result(result).get("agents", []))
        return [
            {
                "instance": a.get("instance"),
                "active": isinstance(a.get("days_ago"), (int, float)) and a["days_ago"] <= 1.0,
                "role_description": a.get("role_description") or "",
            }
            for a in agents
            if a.get("instance")
        ]

    @router.get("/compose", response_class=HTMLResponse)
    async def compose_page(request: Request) -> Any:
        session = store.from_request(request)
        if not session.get("logged_in"):
            return RedirectResponse("/login", status_code=303)
        project = (session.get("project") or "").lower()
        if not project:
            return RedirectResponse("/projects", status_code=303)
        client: MCPClient = request.app.state.mcp
        all_agents = list(
            _unwrap_tool_result(await client.call("memory_list_agents")).get("agents", [])
        )
        projects = sorted(
            {(a.get("project") or "").lower() for a in all_agents if a.get("project")}
        )
        agents_in_project = [
            {
                "instance": a.get("instance"),
                "active": isinstance(a.get("days_ago"), (int, float)) and a["days_ago"] <= 1.0,
            }
            for a in all_agents
            if (a.get("project") or "").lower() == project and a.get("instance")
        ]
        return render(
            request,
            "compose.html",
            project=project,
            projects=projects,
            agents=agents_in_project,
            agents_json=json.dumps(agents_in_project),
            last_send=None,
            error=None,
        )

    @router.post("/compose", response_class=HTMLResponse)
    async def compose_submit(
        request: Request,
        to_project: str = Form(...),
        to_instance: str = Form(...),
        category: str = Form("info"),
        priority: str = Form("normal"),
        message: str = Form(...),
    ) -> Any:
        session = store.from_request(request)
        if not session.get("logged_in"):
            return RedirectResponse("/login", status_code=303)
        client: MCPClient = request.app.state.mcp

        # Build context shared by both render branches below.
        all_agents = list(
            _unwrap_tool_result(await client.call("memory_list_agents")).get("agents", []))
        projects = sorted(
            {(a.get("project") or "").lower() for a in all_agents if a.get("project")}
        )
        agents_in_project = [
            {
                "instance": a.get("instance"),
                "active": isinstance(a.get("days_ago"), (int, float)) and a["days_ago"] <= 1.0,
            }
            for a in all_agents
            if (a.get("project") or "").lower() == to_project.lower() and a.get("instance")
        ]

        try:
            result = await client.call(
                "memory_send_message",
                to_instance=to_instance,
                to_project=to_project.lower(),
                message=message,
                category=category,
                priority=priority,
            )
            payload = _unwrap_tool_result(result)
            last_send = {
                "to_project": to_project.lower(),
                "to_instance": to_instance,
                "category": category,
                "body": message,
                "message_id": payload.get("message_id", "?"),
                "destructive_match": bool(payload.get("destructive_match")),
            }
            error = None
        except Exception as exc:
            last_send = None
            error = f"send failed: {exc}"

        return render(
            request,
            "compose.html",
            project=to_project.lower(),
            projects=projects,
            agents=agents_in_project,
            agents_json=json.dumps(agents_in_project),
            last_send=last_send,
            error=error,
        )

    @router.get("/backlog", response_class=HTMLResponse)
    async def backlog_page(
        request: Request,
        project: str = "",
        status: str = "",
        priority: str = "",
        assigned_to: str = "",
    ) -> Any:
        session = store.from_request(request)
        if not session.get("logged_in"):
            return RedirectResponse("/login", status_code=303)
        client: MCPClient = request.app.state.mcp
        kwargs: dict[str, Any] = {"limit": 100, "include_done": True}
        if project:
            kwargs["project"] = project.lower()
        if status:
            kwargs["status"] = status
        if priority:
            kwargs["priority"] = priority
        if assigned_to:
            kwargs["assigned_to"] = assigned_to
        try:
            payload = _unwrap_tool_result(await client.call("memory_list_backlog", **kwargs))
            items = list(payload.get("items", []))
            error = None
        except Exception as exc:
            items = []
            error = f"backlog fetch failed: {exc}"
        agents_payload = _unwrap_tool_result(await client.call("memory_list_agents"))
        projects = sorted(
            {(a.get("project") or "").lower()
             for a in agents_payload.get("agents", []) if a.get("project")}
        )
        return render(
            request,
            "backlog.html",
            items=items,
            projects=projects,
            filter_project=project,
            filter_status=status,
            filter_priority=priority,
            filter_assigned_to=assigned_to,
            error=error,
        )

    @router.get("/specs", response_class=HTMLResponse)
    async def specs_page(request: Request, project: str = "") -> Any:
        session = store.from_request(request)
        if not session.get("logged_in"):
            return RedirectResponse("/login", status_code=303)
        client: MCPClient = request.app.state.mcp
        kwargs: dict[str, Any] = {}
        if project:
            kwargs["project"] = project.lower()
        try:
            payload = _unwrap_tool_result(await client.call("memory_list_specs", **kwargs))
            specs = list(payload.get("specs", []))
            error = None
        except Exception as exc:
            specs = []
            error = f"specs fetch failed: {exc}"
        agents_payload = _unwrap_tool_result(await client.call("memory_list_agents"))
        projects = sorted(
            {(a.get("project") or "").lower()
             for a in agents_payload.get("agents", []) if a.get("project")}
        )
        return render(
            request,
            "specs.html",
            specs=specs,
            projects=projects,
            filter_project=project,
            error=error,
        )

    @router.get("/specs/view", response_class=HTMLResponse)
    async def spec_view_page(request: Request, name: str, project: str = "") -> Any:
        session = store.from_request(request)
        if not session.get("logged_in"):
            return RedirectResponse("/login", status_code=303)
        client: MCPClient = request.app.state.mcp
        kwargs: dict[str, Any] = {"name": name}
        if project:
            kwargs["project"] = project.lower()
        try:
            spec = _unwrap_tool_result(await client.call("memory_get_spec", **kwargs))
        except Exception as exc:
            spec = {"spec_name": name, "version": "?", "content": f"failed to load: {exc}"}
        return render(request, "spec_view.html", spec=spec)

    @router.get("/", response_class=HTMLResponse)
    async def home(request: Request) -> Any:
        if store.from_request(request).get("logged_in"):
            return RedirectResponse("/projects", status_code=303)
        return RedirectResponse("/login", status_code=303)

    # Re-export for tests that want to call matches_destructive without import gymnastics.
    router.state_destructive = matches_destructive  # type: ignore[attr-defined]

    return router


def _summarize_projects(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group the flat agent list by `project` for the picker view."""
    counts: Counter[str] = Counter()
    active_24h: Counter[str] = Counter()
    for a in agents:
        proj = (a.get("project") or "").lower()
        if not proj:
            continue
        counts[proj] += 1
        last_seen = a.get("days_ago")
        if isinstance(last_seen, (int, float)) and last_seen <= 1.0:
            active_24h[proj] += 1
    return [
        {"name": p, "agent_count": counts[p], "active_24h": active_24h[p]}
        for p in sorted(counts, key=lambda k: (-active_24h[k], -counts[k], k))
    ]


async def _seed_inbox(
    client: MCPClient, broker: InboxBroker, project: str
) -> list[dict[str, Any]]:
    """Initial-page render: fetch ~50 most recent messages across watched agents.

    Always-watch keys (the user's self-inbox) are included regardless of the
    selected project — same bypass principle the broker applies to live pushes.
    Otherwise navigating to /inbox?project=X hides replies addressed to the
    user's runtime-identity mailbox.

    Prefer the inbox resource read over memory_get_messages(for_instance=...)
    because the latter is gated to project admins server-side, and the
    user-tier session is not auto-admin'd — so memory_get_messages returns
    a permission-denied payload that the page silently renders as empty.
    """
    always_watch = set(broker.always_watch_keys)
    seeded: list[dict[str, Any]] = []
    for key in broker.watched_keys:
        if key.project != project and key not in always_watch:
            continue
        try:
            if client.capabilities.inbox_resource_supported:
                payload = await client.read_resource(key.uri())
            else:
                result = await client.call(
                    "memory_get_messages",
                    for_instance=key.agent,
                    limit=20,
                    include_delivered=True,
                )
                payload = _unwrap_tool_result(result)
            for msg in payload.get("messages", []):
                seeded.append({**msg, "agent": key.agent})
        except Exception as exc:
            log.warning("seed_inbox_failed", key=str(key), error=str(exc))
    seeded.sort(key=lambda m: m.get("created") or "", reverse=True)
    return seeded[:50]
