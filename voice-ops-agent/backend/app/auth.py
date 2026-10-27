"""Minimal authentication boundary: one shared manager login using HTTP Basic credentials.

Deliberately simple for the MVP. It has no roles, no per-user audit trail and no rate limiting, and
Basic credentials are only safe over HTTPS. See README, "Authentication boundary".

The 401 response omits the WWW-Authenticate header on purpose so browsers do not show their own
native login prompt; the React app shows its own login form instead.
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from .config import Settings, get_settings

_basic = HTTPBasic(auto_error=False)


def require_auth(
    credentials: HTTPBasicCredentials | None = Depends(_basic),
    settings: Settings = Depends(get_settings),
) -> str:
    if not settings.auth_enabled:
        return "anonymous"
    if credentials is not None:
        user_ok = secrets.compare_digest(credentials.username.encode(), settings.auth_username.encode())
        pass_ok = secrets.compare_digest(credentials.password.encode(), settings.auth_password.encode())
        if user_ok and pass_ok and settings.auth_password:
            return credentials.username
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Authentication required or credentials incorrect.")
