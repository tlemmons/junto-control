from __future__ import annotations

import hmac
import json
import time
from typing import Any

from fastapi import Request
from fastapi.responses import RedirectResponse
from itsdangerous import BadSignature, TimestampSigner

COOKIE_NAME = "cc_session"
COOKIE_MAX_AGE_SEC = 60 * 60 * 24 * 14  # 14 days


class SessionStore:
    """Signed-cookie session: holds {logged_in: bool, project: str | None}."""

    def __init__(self, secret: str) -> None:
        self._signer = TimestampSigner(secret)

    def encode(self, data: dict[str, Any]) -> str:
        raw = json.dumps(data, separators=(",", ":")).encode()
        return self._signer.sign(raw).decode()

    def decode(self, value: str | None) -> dict[str, Any]:
        if not value:
            return {}
        try:
            raw = self._signer.unsign(value, max_age=COOKIE_MAX_AGE_SEC)
            return json.loads(raw)
        except (BadSignature, ValueError, json.JSONDecodeError):
            return {}

    def from_request(self, request: Request) -> dict[str, Any]:
        return self.decode(request.cookies.get(COOKIE_NAME))


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def require_login(request: Request, store: SessionStore) -> dict[str, Any] | RedirectResponse:
    session = store.from_request(request)
    if not session.get("logged_in"):
        return RedirectResponse("/login", status_code=303)
    return session


def issue_cookie(response: Any, store: SessionStore, data: dict[str, Any]) -> None:
    data = {**data, "issued_at": int(time.time())}
    response.set_cookie(
        COOKIE_NAME,
        store.encode(data),
        max_age=COOKIE_MAX_AGE_SEC,
        httponly=True,
        samesite="lax",
        secure=False,  # dev. Production must terminate TLS upstream.
    )


def clear_cookie(response: Any) -> None:
    response.delete_cookie(COOKIE_NAME)
