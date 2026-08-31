"""
Staff login (POST /api/auth/login) and session inspection.

WHY THE COOKIE IS NOT httpOnly
------------------------------
The session cookie is readable by JavaScript. That is a real, deliberate
downgrade and it is worth being precise about the trade:

  * The UI (localhost:3000 / the public site) and the API (localhost:8000)
    are separate origins. An httpOnly cookie would need SameSite=None plus
    credentialed CORS to reach the API at all.
  * Next.js middleware needs to read the cookie server-side to gate /staff
    before a page renders, and the browser API client needs the same value
    to build `Authorization: Bearer`.

Cost: an XSS in the dashboard can steal a session. Mitigations that ARE in
place: a 12-hour expiry, SameSite=Lax, Secure in production, and the fact
that the token carries a hotel_id so a stolen session is confined to one
property. If the API is later served from the same origin as the UI
(same domain, /api path via the reverse proxy), switch this to httpOnly and
drop the Bearer header - the proxy config in infra/docker/ already makes
that possible.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import COOKIE_NAME, Principal, require_staff
from app.core.config import settings
from app.core.db import get_db
from app.core.security import (
    SecurityNotConfigured,
    issue_token,
    verify_password,
    waste_time_like_a_real_verify,
)
from app.platform import schemas
from app.platform.users import User

router = APIRouter(prefix="/auth", tags=["auth"])

# --- brute-force throttle ------------------------------------------------
#
# In-process, so with N uvicorn workers the real ceiling is N * MAX_ATTEMPTS.
# That is honestly weaker than a Redis-backed counter and is a deliberate
# trade for the pilot: it stops a scripted password list without adding a
# hard dependency on Redis to the login path. Move it to Redis before this
# faces the open internet with more than one worker.
MAX_ATTEMPTS = 10
WINDOW_SECONDS = 15 * 60
_attempts: dict[str, deque[float]] = defaultdict(deque)


def _throttle_key(request: Request, email: str) -> str:
    client = request.client.host if request.client else "unknown"
    return f"{client}|{email}"


def _too_many_attempts(key: str) -> bool:
    now = time.monotonic()
    bucket = _attempts[key]
    while bucket and now - bucket[0] > WINDOW_SECONDS:
        bucket.popleft()
    return len(bucket) >= MAX_ATTEMPTS


def _record_attempt(key: str) -> None:
    _attempts[key].append(time.monotonic())


def _clear_attempts(key: str) -> None:
    _attempts.pop(key, None)


# --- login ---------------------------------------------------------------

#: One message for every failure mode. "No such user" and "wrong password"
#: must be indistinguishable, or the form becomes an account enumerator.
_BAD_CREDENTIALS = "Email or password is incorrect."


@router.post("/login", response_model=schemas.LoginOut)
async def login(
    payload: schemas.LoginIn,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    key = _throttle_key(request, payload.email)
    if _too_many_attempts(key):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many sign-in attempts. Try again in 15 minutes.",
        )

    user = (
        await db.execute(select(User).where(User.email == payload.email))
    ).scalar_one_or_none()

    # Verify against a dummy hash when the account is missing, so a bad
    # email costs the same ~250ms as a bad password. Skipping the work
    # would let response time alone reveal which emails are registered.
    if user is None:
        waste_time_like_a_real_verify()
        _record_attempt(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_BAD_CREDENTIALS
        )

    if not verify_password(payload.password, user.hashed_password):
        _record_attempt(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=_BAD_CREDENTIALS
        )

    # Checked AFTER the password, on purpose: answering "this account is
    # disabled" to an unverified caller confirms the account exists.
    if not user.is_active:
        _record_attempt(key)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account has been deactivated. Contact your manager.",
        )

    try:
        token, expires_at = issue_token(
            user_id=user.id,
            email=user.email,
            role=user.role.value,
            hotel_id=user.hotel_id,
        )
    except SecurityNotConfigured as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    _clear_attempts(key)
    user.last_login_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(user)

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=settings.jwt_expire_minutes * 60,
        # Readable by JS by design - see the module docstring.
        httponly=False,
        # Lax, not None: the dashboard is never legitimately loaded from
        # inside someone else's page, so this costs nothing and blocks the
        # cross-site request forgery shape.
        samesite="lax",
        secure=settings.cookie_secure,
        domain=settings.cookie_domain or None,
        path="/",
    )

    return schemas.LoginOut(
        access_token=token,
        expires_at=expires_at,
        user=schemas.UserOut.model_validate(user),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response):
    """Clear the cookie.

    Honest limitation: this is a stateless JWT, so a token already copied
    elsewhere stays valid until it expires. Real revocation needs a
    denylist or short-lived tokens with refresh; neither is worth the
    machinery at pilot scale, and pretending otherwise would be worse than
    saying so. Deactivate the user to cut access off at the next login.
    """
    response.delete_cookie(
        key=COOKIE_NAME,
        samesite="lax",
        secure=settings.cookie_secure,
        domain=settings.cookie_domain or None,
        path="/",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=schemas.SessionOut)
async def me(
    principal: Principal = Depends(require_staff),
    db: AsyncSession = Depends(get_db),
):
    """Who the server thinks is calling.

    The frontend uses this to confirm a cookie is still good rather than
    trusting its own decode - a token the client considers fine may be
    signed with a rotated secret, or belong to a deactivated user.
    """
    user = None
    if principal.user_id is not None:
        row = (
            await db.execute(select(User).where(User.id == principal.user_id))
        ).scalar_one_or_none()
        # A token outliving its user is a real case: the account was
        # deleted or deactivated mid-session.
        if row is None or not row.is_active:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="This account is no longer active. Please sign in again.",
            )
        user = schemas.UserOut.model_validate(row)

    return schemas.SessionOut(
        authenticated=True,
        method=principal.method,
        user=user,
        hotel_id=principal.hotel_id,
        role=principal.role,
    )
