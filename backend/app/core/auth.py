"""
Who is calling, and may they see this hotel's data.

Two credentials are accepted, and they are NOT equivalent:

  1. A staff JWT (Authorization: Bearer, or the session cookie). Carries a
     user id, a role and a hotel_id, so the request can be scoped to one
     property and attributed to one person.

  2. The legacy shared `STAFF_API_TOKEN`. Carries no identity at all. It
     is kept working so an existing deployment does not break mid-upgrade,
     and so the eval harness and seed scripts have a way in without a
     password. It grants CROSS-TENANT access because there is nothing in
     it to scope by - which is exactly why it should be unset in
     production now that real accounts exist.

FAILS CLOSED. If neither credential is configured server-side, the gated
endpoints return 503 rather than allowing the request. A gate that
disappears when misconfigured is how an open dashboard reaches production.
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass

from fastapi import Cookie, Depends, Header, HTTPException, Request, status

from app.core.config import settings
from app.core.security import (
    InvalidToken,
    SecurityNotConfigured,
    auth_configured,
    decode_token,
)

HEADER_NAME = "X-Staff-Token"
#: Read by Next.js middleware to gate /staff routes, and by the browser API
#: client to build the Authorization header. Not httpOnly - see the note in
#: app/platform/api/auth_routes.py for why, and what it costs.
COOKIE_NAME = "hos_staff_session"

SHARED_TOKEN_ROLE = "shared_token"


@dataclass(frozen=True)
class Principal:
    """The authenticated caller.

    `hotel_id is None` means cross-tenant: a platform admin, or the
    identity-less shared token. Everything else is pinned to one property.
    """

    user_id: int | None
    email: str
    role: str
    hotel_id: int | None
    method: str  # "jwt" | "shared_token"

    @property
    def is_cross_tenant(self) -> bool:
        return self.hotel_id is None

    def may_access(self, hotel_id: int) -> bool:
        return self.is_cross_tenant or self.hotel_id == hotel_id

    def assert_may_access(self, hotel_id: int) -> None:
        """404, not 403, on a mismatch.

        Deliberate: 403 confirms the hotel exists, which lets a logged-in
        user at one property enumerate the others. To a caller with no
        business here, the resource should simply not be there (NFR-3).
        """
        if not self.may_access(hotel_id):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Hotel {hotel_id} not found",
            )


def _unauthorised(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer(authorization: str) -> str:
    scheme, _, token = (authorization or "").partition(" ")
    return token.strip() if scheme.lower() == "bearer" else ""


async def require_staff(
    request: Request,
    authorization: str = Header(default=""),
    x_staff_token: str = Header(default="", alias=HEADER_NAME),
    session_cookie: str = Cookie(default="", alias=COOKIE_NAME),
) -> Principal:
    """Resolve the caller, or refuse.

    Order matters: a JWT is tried first so that a deployment which still
    has STAFF_API_TOKEN set does not silently downgrade a real, scoped
    staff session to identity-less cross-tenant access.
    """
    shared_secret = (settings.staff_api_token or "").strip()

    # --- 1. staff JWT ---
    token = _bearer(authorization) or session_cookie.strip()
    if token:
        if not auth_configured():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "A session token was sent but JWT_SECRET is not "
                    "configured, so it cannot be verified. Set it and restart."
                ),
            )
        try:
            claims = decode_token(token)
        except InvalidToken as exc:
            raise _unauthorised(
                "Session is invalid or has expired. Please sign in again."
            ) from exc

        principal = Principal(
            user_id=claims.user_id,
            email=claims.email,
            role=claims.role,
            hotel_id=claims.hotel_id,
            method="jwt",
        )
        request.state.principal = principal
        return principal

    # --- 2. legacy shared token ---
    supplied = (x_staff_token or "").strip()
    if supplied:
        if not shared_secret:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    f"{HEADER_NAME} was sent but STAFF_API_TOKEN is not "
                    "configured. Sign in with a staff account instead."
                ),
            )
        # compare_digest: a short-circuiting == leaks the prefix by timing.
        if not secrets.compare_digest(supplied, shared_secret):
            raise _unauthorised(f"Missing or invalid {HEADER_NAME}.")

        principal = Principal(
            user_id=None,
            email="",
            role=SHARED_TOKEN_ROLE,
            hotel_id=None,  # nothing to scope by
            method="shared_token",
        )
        request.state.principal = principal
        return principal

    # --- 3. nothing supplied ---
    if not auth_configured() and not shared_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Staff access is not configured, so these endpoints are "
                "disabled. Set JWT_SECRET in .env, run the admin seed "
                "script, and restart."
            ),
        )
    raise _unauthorised("Sign in to access the staff dashboard.")


async def require_staff_token(
    principal: Principal = Depends(require_staff),
) -> Principal:
    """Backwards-compatible name.

    The staff router still mounts this, and older callers import it. It is
    now just require_staff - renaming at every call site would make a
    security change look like a refactor in the diff.
    """
    return principal


def require_platform_admin(
    principal: Principal = Depends(require_staff),
) -> Principal:
    """For endpoints that manage accounts or cross every tenant."""
    from app.platform.users import UserRole

    if principal.role not in (UserRole.platform_admin.value, SHARED_TOKEN_ROLE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action requires a platform administrator.",
        )
    return principal


__all__ = [
    "COOKIE_NAME",
    "HEADER_NAME",
    "Principal",
    "SHARED_TOKEN_ROLE",
    "SecurityNotConfigured",
    "require_platform_admin",
    "require_staff",
    "require_staff_token",
]
