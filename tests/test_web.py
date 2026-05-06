from __future__ import annotations

from juntocontrol.auth import SessionStore, constant_time_eq
from juntocontrol.web import _summarize_projects


def test_constant_time_eq() -> None:
    assert constant_time_eq("abc", "abc")
    assert not constant_time_eq("abc", "abd")
    assert not constant_time_eq("abc", "abcd")


def test_session_store_roundtrip() -> None:
    store = SessionStore("secret-key" * 4)
    cookie = store.encode({"logged_in": True, "project": "nimbus"})
    decoded = store.decode(cookie)
    assert decoded["logged_in"] is True
    assert decoded["project"] == "nimbus"


def test_session_store_rejects_tampered() -> None:
    store = SessionStore("secret-key" * 4)
    cookie = store.encode({"logged_in": True})
    # Flip a byte inside the signature segment (after the last dot) so the HMAC fails
    # deterministically. Mutating the payload region can occasionally still yield a
    # decodable timestamp, so the right place to corrupt is the trailing signature.
    last_dot = cookie.rfind(".")
    bad = cookie[:last_dot + 1] + "x" + cookie[last_dot + 2 :]
    assert store.decode(bad) == {}

    # Also verify a different secret rejects a cookie signed with the original.
    other = SessionStore("different-secret" * 2)
    assert other.decode(cookie) == {}


def test_summarize_projects_groups_and_counts() -> None:
    agents = [
        {"project": "nimbus", "instance": "main", "days_ago": 0.1},
        {"project": "Nimbus", "instance": "frames-team", "days_ago": 5.0},
        {"project": "claudecontrol", "instance": "claude-control", "days_ago": 0.0},
        {"project": "", "instance": "ghost"},
    ]
    out = _summarize_projects(agents)
    by_name = {p["name"]: p for p in out}
    assert "nimbus" in by_name
    assert by_name["nimbus"]["agent_count"] == 2  # case-folded
    assert by_name["nimbus"]["active_24h"] == 1
    assert by_name["claudecontrol"]["agent_count"] == 1
    assert "" not in by_name


def test_summarize_projects_sort_active_first() -> None:
    agents = [
        {"project": "stale", "instance": "old", "days_ago": 30.0},
        {"project": "stale", "instance": "old2", "days_ago": 30.0},
        {"project": "fresh", "instance": "now", "days_ago": 0.05},
    ]
    out = _summarize_projects(agents)
    # fresh has 1 active in 24h, stale has 0 — fresh ranks first.
    assert out[0]["name"] == "fresh"
    assert out[1]["name"] == "stale"
