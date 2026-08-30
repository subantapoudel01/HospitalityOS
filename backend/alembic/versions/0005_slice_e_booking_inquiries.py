"""Slice E - booking_inquiries

Revision ID: 0005
Revises: 0004
Create Date: 2026-08-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STATUSES = ("new", "contacted", "confirmed", "lost")


def upgrade() -> None:
    op.create_table(
        "booking_inquiries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("hotel_id", sa.Integer(), nullable=False),
        sa.Column("guest_id", sa.Integer(), nullable=True),
        sa.Column("check_in_date", sa.Date(), nullable=False),
        sa.Column("check_out_date", sa.Date(), nullable=False),
        sa.Column("guest_count", sa.Integer(), nullable=False),
        sa.Column("room_type_preference", sa.String(length=120), nullable=True),
        sa.Column("status", sa.Enum(*STATUSES, name="inquiry_status"),
                  nullable=False),
        sa.Column("raw_request", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"],
                                ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["hotel_id"], ["hotels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.id"], ondelete="SET NULL"),
        # Dates are validated in the service, but a database-level guard
        # means a future caller cannot store a stay that ends before it
        # starts no matter what a model hallucinated.
        sa.CheckConstraint("check_out_date > check_in_date",
                           name="ck_booking_dates_ordered"),
        sa.CheckConstraint("guest_count >= 1", name="ck_booking_guest_count"),
    )
    op.create_index("ix_booking_inquiries_hotel_id", "booking_inquiries",
                    ["hotel_id"])
    op.create_index("ix_booking_inquiries_conversation_id", "booking_inquiries",
                    ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_booking_inquiries_conversation_id",
                  table_name="booking_inquiries")
    op.drop_index("ix_booking_inquiries_hotel_id", table_name="booking_inquiries")
    op.drop_table("booking_inquiries")
    sa.Enum(name="inquiry_status").drop(op.get_bind(), checkfirst=True)
