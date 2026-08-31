"""Record why a conversation was escalated

Revision ID: 0008
Revises: 0007
Create Date: 2026-08-31

Escalation used to be guest-initiated only, so "why" was always the same
answer: they asked. US-4 adds automatic escalation on detected frustration
or on a run of dead ends, and now the reason varies - which changes what
staff need to see before they open a transcript.

NULL means the guest asked for a human themselves, which is both the
historical case for existing rows and the correct reading of them.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        # Text, not an enum: this is a sentence shown to a person, and the
        # set of things worth saying will grow. An enum would mean a
        # migration every time the wording changes.
        sa.Column("escalation_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversations",
        # The machine-readable half: "frustration", "dead_end", or NULL for
        # guest-initiated. Kept separate from the sentence so the dashboard
        # can filter and count without parsing prose.
        sa.Column("escalation_trigger", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "escalation_trigger")
    op.drop_column("conversations", "escalation_reason")
