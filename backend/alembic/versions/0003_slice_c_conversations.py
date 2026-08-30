"""Slice C - conversations: guests, conversations, messages, ai_requests

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-20
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CHANNELS = ("website", "whatsapp")
STATUSES = ("active", "escalated", "resolved")
SENDERS = ("guest", "ai", "staff")
PURPOSES = ("chat", "classification", "embedding")


def upgrade() -> None:
    op.create_table(
        "guests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("preferred_language", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )
    op.create_index("ix_guests_phone", "guests", ["phone"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("hotel_id", sa.Integer(), nullable=False),
        sa.Column("guest_id", sa.Integer(), nullable=True),
        sa.Column("channel", sa.Enum(*CHANNELS, name="conversation_channel"),
                  nullable=False),
        sa.Column("status", sa.Enum(*STATUSES, name="conversation_status"),
                  nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["hotel_id"], ["hotels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["guest_id"], ["guests.id"], ondelete="SET NULL"),
    )
    op.create_index("ix_conversations_hotel_id", "conversations", ["hotel_id"])
    op.create_index("ix_conversations_guest_id", "conversations", ["guest_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=False),
        sa.Column("sender", sa.Enum(*SENDERS, name="message_sender"),
                  nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("language_detected", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"],
                                ondelete="CASCADE"),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])

    op.create_table(
        "ai_requests",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("model_used", sa.String(length=120), nullable=False),
        sa.Column("purpose", sa.Enum(*PURPOSES, name="ai_request_purpose"),
                  nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("cost_estimate", sa.Float(), nullable=True),
        sa.Column("retrieved_chunk_ids", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"],
                                ondelete="CASCADE"),
    )
    op.create_index("ix_ai_requests_conversation_id", "ai_requests",
                    ["conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_ai_requests_conversation_id", table_name="ai_requests")
    op.drop_table("ai_requests")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_guest_id", table_name="conversations")
    op.drop_index("ix_conversations_hotel_id", table_name="conversations")
    op.drop_table("conversations")
    op.drop_index("ix_guests_phone", table_name="guests")
    op.drop_table("guests")
    bind = op.get_bind()
    for name in ("ai_request_purpose", "message_sender",
                 "conversation_status", "conversation_channel"):
        sa.Enum(name=name).drop(bind, checkfirst=True)
