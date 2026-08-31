"""
Create or update a staff account, so nobody is locked out of the dashboard.

    docker compose -f infra/docker/docker-compose.yml run --rm backend \
        python -m app.scripts.seed_admin --email admin@rupakot.com --hotel-id 1

    # or, from the Makefile
    make seed-admin

WHAT IT DOES NOT DO: ship a default password. There is no fallback value
like "admin123" anywhere in this file. If --password is omitted a random
one is generated and printed ONCE, because a seed script with a known
default password is the single most reliable way to end up with a
production dashboard anyone can open. The same reasoning applies to
JWT_SECRET, which this script will generate for you but never invent
silently at runtime.

Re-running is safe. An existing account is updated, not duplicated, and its
password is left alone unless --password or --reset-password is given.
"""
from __future__ import annotations

import argparse
import asyncio
import secrets
import string
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.security import MAX_PASSWORD_BYTES, hash_password
from app.platform.models import Hotel
from app.platform.schemas import normalise_email
from app.platform.users import CROSS_TENANT_ROLES, User, UserRole

# Ambiguous glyphs removed: someone will read this off a screen and type it
# into a phone, and O/0 and l/1/I is where that goes wrong.
_ALPHABET = (
    "".join(c for c in string.ascii_letters + string.digits if c not in "O0lI1")
    + "!@#$%^&*-_=+"
)


def generate_password(length: int = 20) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def _session_maker() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(settings.database_url)
    return async_sessionmaker(engine, expire_on_commit=False)


async def seed(
    *,
    email: str,
    password: str | None,
    hotel_id: int | None,
    role: UserRole,
    full_name: str | None,
    reset_password: bool,
) -> int:
    email = normalise_email(email)

    if hotel_id is None and role not in CROSS_TENANT_ROLES:
        print(
            f"error: role '{role.value}' must be scoped to a hotel. Pass "
            "--hotel-id, or use --role platform_admin if this account really "
            "should see every property's guest transcripts.",
            file=sys.stderr,
        )
        return 2

    maker = _session_maker()
    async with maker() as db:
        if hotel_id is not None:
            hotel = (
                await db.execute(select(Hotel).where(Hotel.id == hotel_id))
            ).scalar_one_or_none()
            if hotel is None:
                # Fail rather than create one. An account pointing at a
                # hotel that does not exist logs in fine and then shows an
                # empty dashboard, which is a confusing way to find out.
                print(
                    f"error: hotel {hotel_id} does not exist. Create the "
                    "property at /setup first, or pass a different --hotel-id.",
                    file=sys.stderr,
                )
                return 2
            scope = f"{hotel.name} (hotel {hotel.id})"
        else:
            scope = "ALL hotels (cross-tenant)"

        user = (
            await db.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()

        generated: str | None = None
        if password is None and (user is None or reset_password):
            generated = generate_password()
            password = generated

        if user is None:
            user = User(
                email=email,
                hashed_password=hash_password(password),
                full_name=full_name,
                hotel_id=hotel_id,
                role=role,
            )
            db.add(user)
            action = "created"
        else:
            user.hotel_id = hotel_id
            user.role = role
            user.is_active = True
            if full_name:
                user.full_name = full_name
            if password is not None:
                user.hashed_password = hash_password(password)
                action = "updated (password changed)"
            else:
                # The common re-run: fix the role or re-enable the account
                # without disturbing a password the user already knows.
                action = "updated (password unchanged)"

        await db.commit()
        await db.refresh(user)

    print(f"\n  {action}: {user.email}")
    print(f"  role:     {user.role.value}")
    print(f"  scope:    {scope}")
    if generated:
        print(f"\n  PASSWORD: {generated}")
        print("  Shown once and not stored anywhere in readable form.")
        print("  Copy it now, then change it after the first sign-in.\n")
    print("  Sign in at http://localhost:3000/staff/login\n")

    if not (settings.jwt_secret or "").strip():
        print(
            "  WARNING: JWT_SECRET is not set, so login will return 503 even\n"
            "  with this account in place. Generate one:\n\n"
            "      python -m app.scripts.seed_admin --print-secret\n\n"
            "  then add it to .env and restart the backend.\n",
            file=sys.stderr,
        )
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--email", default="admin@rupakot.com")
    ap.add_argument(
        "--password",
        default=None,
        help="Omit to generate a random one and print it once.",
    )
    ap.add_argument(
        "--hotel-id",
        type=int,
        default=1,
        help="Property this account is scoped to. Use --hotel-id 0 with "
        "--role platform_admin for cross-tenant access.",
    )
    ap.add_argument(
        "--role",
        default=UserRole.admin.value,
        choices=[r.value for r in UserRole],
    )
    ap.add_argument("--full-name", default="Resort Administrator")
    ap.add_argument(
        "--reset-password",
        action="store_true",
        help="Generate a new password for an account that already exists.",
    )
    ap.add_argument(
        "--print-secret",
        action="store_true",
        help="Print a fresh JWT_SECRET for .env and exit. Touches no database.",
    )
    args = ap.parse_args()

    if args.print_secret:
        print(f"JWT_SECRET={secrets.token_urlsafe(48)}")
        return 0

    if args.password is not None:
        if len(args.password.encode("utf-8")) > MAX_PASSWORD_BYTES:
            print(
                f"error: password must be at most {MAX_PASSWORD_BYTES} bytes "
                "(bcrypt truncates beyond that, silently ignoring the rest).",
                file=sys.stderr,
            )
            return 2
        if len(args.password) < 12:
            print(
                "error: password must be at least 12 characters. Omit "
                "--password entirely to get a generated one.",
                file=sys.stderr,
            )
            return 2

    return asyncio.run(
        seed(
            email=args.email,
            password=args.password,
            hotel_id=None if args.hotel_id == 0 else args.hotel_id,
            role=UserRole(args.role),
            full_name=args.full_name,
            reset_password=args.reset_password,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
