"""
Staff accounts - platform level, because a user is not receptionist-only.

This is what replaces the shared `STAFF_API_TOKEN` gate. That gate had no
identity: every staff member was the same anonymous bearer of one secret,
so a transcript read left no trace of who read it and revoking one person's
access meant rotating the secret for everyone. See app/core/auth.py.

`hotel_id` is the tenant boundary (NFR-3). It is nullable ONLY for the
platform-admin role, which is deliberately not what the seeded account
uses - a real staff login is always scoped to one property, and a token
that can read every hotel's guest transcripts should be an explicit
decision rather than the default.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserRole(str, enum.Enum):
    """Fixed set. Staff pick from these, they don't invent them.

    `staff` and `manager` are separated now because the permission split
    lands later (who may delete a conversation, who may edit the knowledge
    base) and retrofitting a role column onto live accounts is worse than
    carrying an unused distinction.
    """

    # Full access within one hotel: dashboard, setup, knowledge base.
    admin = "admin"
    # Reserved for the permission split; currently identical to admin.
    manager = "manager"
    # Reserved for the permission split; currently identical to admin.
    staff = "staff"
    # Cross-tenant. The only role permitted a NULL hotel_id.
    platform_admin = "platform_admin"


#: Roles allowed to hold a NULL hotel_id, i.e. to see every property.
CROSS_TENANT_ROLES = frozenset({UserRole.platform_admin})


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored lower-cased and stripped (see schemas.normalise_email) so
    # "Admin@Rupakot.com" and "admin@rupakot.com" cannot become two
    # accounts with different passwords.
    email: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    # bcrypt output, ~60 chars. Named hashed_password rather than
    # `password` so no code path can plausibly mistake it for a plaintext.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    hotel_id: Mapped[int | None] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=True, index=True
    )
    role: Mapped[UserRole] = mapped_column(
        Enum(
            UserRole,
            name="user_role",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=UserRole.staff,
    )
    # Deactivating beats deleting: a departed employee's name should stay
    # readable on the conversations they handled.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<User {self.email} role={self.role.value} hotel={self.hotel_id}>"
