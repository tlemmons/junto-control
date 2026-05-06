from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    mcp_url: str
    tom_web_api_key: str
    agent_name: str
    project: str
    session_secret: str
    login_passphrase: str
    host: str
    port: int
    log_level: str

    @classmethod
    def from_env(cls) -> Settings:
        def required(key: str) -> str:
            value = os.environ.get(key, "").strip()
            if not value:
                raise RuntimeError(f"missing required env var: {key}")
            return value

        return cls(
            mcp_url=os.environ.get("MCP_URL", "http://localhost:8080/mcp").strip(),
            tom_web_api_key=required("TOM_WEB_API_KEY"),
            agent_name=os.environ.get("JUNTOCONTROL_AGENT_NAME", "claude-control").strip(),
            project=os.environ.get("JUNTOCONTROL_PROJECT", "claudecontrol").strip(),
            session_secret=required("SESSION_SECRET"),
            login_passphrase=required("LOGIN_PASSPHRASE"),
            host=os.environ.get("HOST", "0.0.0.0").strip(),  # noqa: S104  intentional: container bind
            port=int(os.environ.get("PORT", "8000")),
            log_level=os.environ.get("LOG_LEVEL", "INFO").strip().upper(),
        )
