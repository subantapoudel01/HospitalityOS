"""Staff accounts (users table)

Revision ID: 0007
Revises: 0006
Create Date: 2026-08-31

Replaces the shared STAFF_API_TOKEN gate with real per-person accounts.
The old gate had no identity: everyone was the same anonymous bearer of one
secret, so reading a guest transcript left no trace of who read it and
revoking one person meant rotating the secret for all of them.

`hotel_id` is nullable because the platform_admin role is cross-tenant.
Every other role is expected to carry one, and app/platform/users.py names
which roles may legitimately be NULL. That rule is not a CHECK constraint
on purpose - roles will gain members, and a migration to relax a constraint
is a worse failure mode than a documented invariant enforced in one place.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

USER_ROLES = ("admin", "manager", "staff", "platform_admin")


def upgrade() -> None:
    # Create the type explicitly, then reference it with create_type=False.
    # A bare sa.Enum() inside create_table also emits CREATE TYPE, so doing
    # both raises DuplicateObjectError - and checkfirst on the first call
    # does not help, because it is the second, implicit one that fails.
    user_role = postgresql.ENUM(*USER_ROLES, name="user_role", create_type=False)
    sa.Enum(*USER_ROLES, name="user_role").create(op.get_bind(), checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Stored lower-cased (see schemas.normalise_email). The unique
        # index is case-SENSITIVE, so without that normalisation
        # "Admin@x.com" would become a second account nobody can log into.
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("hashed_password", sa.String(length=255), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=True),
        sa.Column(
            "hotel_id",
            sa.Integer(),
            sa.ForeignKey("hotels.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("role", user_role, nullable=False, server_default="staff"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)
    op.create_index("ix_users_hotel_id", "users", ["hotel_id"])


def downgrade() -> None:
    op.drop_index("ix_users_hotel_id", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
    sa.Enum(name="user_role").drop(op.get_bind(), checkfirst=True)
