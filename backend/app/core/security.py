"""
Password hashing and JWT issuing/verification.

Kept separate from app/core/auth.py on purpose: this module knows how to
make and check credentials, auth.py knows who is allowed through a given
endpoint. Mixing them is how a permission check ends up quietly re-deriving
a token.

FAILS CLOSED, like the staff token gate before it. An unset JWT_SECRET
disables login entirely (503) rather than falling back to a built-in
default. A hardcoded development secret is the single most common way a
JWT deployment ends up forgeable in production: it ships, nobody changes
it, and the value is in the public repo.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

# bcrypt directly, NOT passlib.
#
# passlib[bcrypt] is the usual recommendation and was the first thing tried
# here. passlib 1.7.4 (last released 2020) reads `bcrypt.__about__`, which
# bcrypt removed in 4.1, so every hash logs an AttributeError traceback
# before falling back. The options were pinning bcrypt to a 2022 release to
# satisfy an unmaintained wrapper, or calling the maintained library
# directly. The wrapper was buying us nothing here - one algorithm, no
# migration between schemes.
#
# The output is ordinary `$2b$` bcrypt either way, so these hashes stay
# readable by passlib if it is ever reintroduced.
#
# Deliberately slow and salted per hash. Never reach for hashlib here.
_ROUNDS = 12

ALGORITHM = "HS256"

#: bcrypt silently truncates at 72 BYTES. Rejecting longer input is better
#: than accepting a password whose tail is ignored, which would make
#: "<72 chars>x" and "<72 chars>y" the same credential.
MAX_PASSWORD_BYTES = 72


class SecurityNotConfigured(RuntimeError):
    """JWT_SECRET is missing, so tokens can be neither issued nor trusted."""


def _secret() -> str:
    value = (settings.jwt_secret or "").strip()
    if not value:
        raise SecurityNotConfigured(
            "JWT_SECRET is not configured, so staff login is disabled. "
            "Generate one with `python -m app.scripts.seed_admin --print-secret` "
            "and set it in .env, then restart."
        )
    return value


def auth_configured() -> bool:
    """Whether login can work at all. Used for health/diagnostics, never
    as a permission check - the check is `_secret()` raising."""
    return bool((settings.jwt_secret or "").strip())


# --- passwords -----------------------------------------------------------


def hash_password(plain: str) -> str:
    encoded = plain.encode("utf-8")
    if len(encoded) > MAX_PASSWORD_BYTES:
        raise ValueError(
            f"Password must be at most {MAX_PASSWORD_BYTES} bytes; bcrypt "
            "truncates beyond that, which would silently ignore the rest."
        )
    return bcrypt.hashpw(encoded, bcrypt.gensalt(rounds=_ROUNDS)).decode("ascii")


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time comparison inside bcrypt itself.

    Never raises. A corrupted or empty hash must read as 'wrong password',
    not as a 500 - a 500 where a 401 belongs tells an attacker the account
    exists.
    """
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError, AttributeError):
        # Malformed hash, wrong type, or a password over 72 bytes that
        # bcrypt refuses outright. All of them mean 'not a match'.
        return False


#: A hash of a value nobody holds. Verified against when the email is
#: unknown, so a missing account costs the same ~250ms as a wrong password.
#: Without it, response time alone enumerates registered emails.
_DUMMY_HASH = bcrypt.hashpw(
    b"not-a-real-password-timing-equaliser", bcrypt.gensalt(rounds=_ROUNDS)
)


def waste_time_like_a_real_verify() -> None:
    bcrypt.checkpw(b"wrong", _DUMMY_HASH)


# --- tokens --------------------------------------------------------------


@dataclass(frozen=True)
class TokenClaims:
    user_id: int
    email: str
    role: str
    hotel_id: int | None
    expires_at: datetime


class InvalidToken(Exception):
    """Signature, expiry or shape is wrong. Deliberately says no more."""


def issue_token(
    *, user_id: int, email: str, role: str, hotel_id: int | None
) -> tuple[str, datetime]:
    """Return (token, expiry). Expiry is returned so the caller can set a
    cookie whose lifetime matches the token's, rather than guessing."""
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id),  # RFC 7519 says sub is a string
        "email": email,
        "role": role,
        "hotel_id": hotel_id,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, _secret(), algorithm=ALGORITHM), expires_at


def decode_token(token: str) -> TokenClaims:
    """Verify and unpack. Raises InvalidToken for anything unusable.

    `algorithms=[ALGORITHM]` is not decoration: accepting the token's own
    `alg` header is the classic JWT forgery (alg=none, or HS256 verified
    against an RS256 public key).
    """
    try:
        payload = jwt.decode(
            token,
            _secret(),
            algorithms=[ALGORITHM],
            options={"require": ["exp", "sub"]},
        )
    except SecurityNotConfigured:
        raise
    except jwt.PyJWTError as exc:
        raise InvalidToken(str(exc)) from exc

    try:
        user_id = int(payload["sub"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidToken("token has no usable subject") from exc

    hotel_id = payload.get("hotel_id")
    if hotel_id is not None and not isinstance(hotel_id, int):
        raise InvalidToken("hotel_id claim is not an integer")

    return TokenClaims(
        user_id=user_id,
        email=str(payload.get("email") or ""),
        role=str(payload.get("role") or ""),
        hotel_id=hotel_id,
        expires_at=datetime.fromtimestamp(payload["exp"], tz=timezone.utc),
    )
