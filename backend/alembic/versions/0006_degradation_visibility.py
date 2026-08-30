"""Add ai_requests.degraded_from

Revision ID: 0006
Revises: 0005
Create Date: 2026-08-24

When a model call fails the system falls back to deterministic rules and
keeps answering. That is the right behaviour, but until now it left no
trace: booking requests quietly became refusals and Nepali questions were
searched untranslated, with nothing in the API response, the dashboard or
this table to say so.

NULL means the turn ran normally. A value names the provider that failed.
Deliberately running without a provider is NOT degradation and stays NULL.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_requests",
        sa.Column("degraded_from", sa.String(length=40), nullable=True),
    )
    # The dashboard asks "did anything degrade in the last 15 minutes" on
    # every poll, so the time filter needs to be cheap.
    op.create_index(
        "ix_ai_requests_created_at", "ai_requests", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_ai_requests_created_at", table_name="ai_requests")
    op.drop_column("ai_requests", "degraded_from")
