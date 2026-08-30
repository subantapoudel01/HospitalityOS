"""Slice D - add 'translation' to ai_request_purpose

Revision ID: 0004
Revises: 0003
Create Date: 2026-08-23

Translation is about to become the highest-volume model call in the system:
every Nepali turn spends one. Folding it under 'classification' would make
per-purpose cost attribution useless, which is the entire reason the
ai_requests table exists (NFR-4).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD = ("chat", "classification", "embedding")
NEW = ("chat", "classification", "embedding", "translation")


def upgrade() -> None:
    # Postgres allows ADD VALUE inside a transaction since 12, provided the
    # new value is not also *used* in the same transaction. Adding only.
    op.execute("ALTER TYPE ai_request_purpose ADD VALUE IF NOT EXISTS 'translation'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum, so the type is rebuilt.
    # Any rows already using it are folded back into 'classification'
    # rather than blocking the downgrade.
    op.execute(
        "UPDATE ai_requests SET purpose = 'classification' "
        "WHERE purpose = 'translation'"
    )
    op.execute("ALTER TYPE ai_request_purpose RENAME TO ai_request_purpose_old")
    sa.Enum(*OLD, name="ai_request_purpose").create(op.get_bind())
    op.execute(
        "ALTER TABLE ai_requests ALTER COLUMN purpose TYPE ai_request_purpose "
        "USING purpose::text::ai_request_purpose"
    )
    op.execute("DROP TYPE ai_request_purpose_old")
