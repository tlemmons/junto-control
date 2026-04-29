from __future__ import annotations

import pytest

from claudecontrol.config import Settings


def test_from_env_requires_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TOM_WEB_API_KEY", raising=False)
    monkeypatch.setenv("SESSION_SECRET", "x" * 32)
    with pytest.raises(RuntimeError, match="TOM_WEB_API_KEY"):
        Settings.from_env()


def test_from_env_requires_session_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOM_WEB_API_KEY", "key")
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        Settings.from_env()


def test_from_env_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOM_WEB_API_KEY", "k")
    monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.setenv("LOGIN_PASSPHRASE", "p")
    s = Settings.from_env()
    assert s.mcp_url == "http://localhost:8080/mcp"
    assert s.agent_name == "claude-control"
    assert s.project == "claudecontrol"
    assert s.port == 8000
    assert s.log_level == "INFO"


def test_lowercase_project_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOM_WEB_API_KEY", "k")
    monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.setenv("LOGIN_PASSPHRASE", "p")
    s = Settings.from_env()
    assert s.project == "claudecontrol"
    assert s.project == s.project.lower()


def test_from_env_requires_login_passphrase(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TOM_WEB_API_KEY", "k")
    monkeypatch.setenv("SESSION_SECRET", "s")
    monkeypatch.delenv("LOGIN_PASSPHRASE", raising=False)
    with pytest.raises(RuntimeError, match="LOGIN_PASSPHRASE"):
        Settings.from_env()
