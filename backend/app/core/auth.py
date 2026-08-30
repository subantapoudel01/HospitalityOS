"""
Staff access gate.

This is a shared-secret deployment gate, NOT authentication. It has no
per-user identity, no audit trail, and anyone holding the token has
everything. It exists because the staff dashboard exposes complete guest
conversation transcripts, and shipping that on a public URL with no gate at
all would be worse. Real auth - users, password hashing, sessions - is still
outstanding and is what should replace this before the pilot (NFR-3).

Fails CLOSED. If STAFF_API_TOKEN is unset the gated endpoints return 503
rather than allowing the request, because a gate that silently disappears
when misconfigured is how an open dashboard reaches production.
"""
from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.core.config import settings

HEADER_NAME = "X-Staff-Token"


async def require_staff_token(
    x_staff_token: str = Header(default="", alias=HEADER_NAME),
) -> None:
    configured = (settings.staff_api_token or "").strip()

    if not configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "STAFF_API_TOKEN is not configured, so staff endpoints are "
                "disabled. Set it in .env and restart."
            ),
        )

    supplied = (x_staff_token or "").strip()
    # compare_digest to keep the comparison constant-time; a short-circuiting
    # == leaks the token prefix through timing.
    if not supplied or not secrets.compare_digest(supplied, configured):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing or invalid {HEADER_NAME}.",
            headers={"WWW-Authenticate": HEADER_NAME},
        )
