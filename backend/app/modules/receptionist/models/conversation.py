"""
Conversation models: the guest-facing transcript and its AI telemetry.

Scoped to the receptionist module. Conversations foreign-key into platform
data (hotels, guests); that direction is allowed, the reverse is not.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Channel(str, enum.Enum):
    website = "website"
    whatsapp = "whatsapp"


class ConversationStatus(str, enum.Enum):
    active = "active"
    escalated = "escalated"
    resolved = "resolved"


class Sender(str, enum.Enum):
    guest = "guest"
    ai = "ai"
    staff = "staff"


class AiPurpose(str, enum.Enum):
    chat = "chat"
    classification = "classification"
    embedding = "embedding"
    # Added in Slice D (migration 0004). Translation is the highest-volume
    # model call once Nepali is live, so it gets its own bucket rather than
    # being hidden inside 'classification'.
    translation = "translation"


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    hotel_id: Mapped[int] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Nullable: a website visitor has no phone number and therefore no guest
    # record. WhatsApp (Slice F) is where guests actually get identified.
    guest_id: Mapped[int | None] = mapped_column(
        ForeignKey("guests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    channel: Mapped[Channel] = mapped_column(
        Enum(Channel, name="conversation_channel",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False, default=Channel.website,
    )
    status: Mapped[ConversationStatus] = mapped_column(
        Enum(ConversationStatus, name="conversation_status",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False, default=ConversationStatus.active,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Why this was escalated (US-4). NULL means the guest asked for a
    # human themselves, which is both the historical case and the correct
    # reading of every row that predates automatic escalation.
    escalation_trigger: Mapped[str | None] = mapped_column(
        String(32), nullable=True
    )
    escalation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="Message.id",
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    sender: Mapped[Sender] = mapped_column(
        Enum(Sender, name="message_sender",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    # Always "en" for Slice C. Slice D populates this from real detection
    # rather than assumption, which is why it is stored per message and not
    # once per conversation - guests switch language mid-thread.
    language_detected: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class AiRequest(Base):
    """One row per model call: the raw material for NFR-4 cost tracking."""

    __tablename__ = "ai_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=True, index=True,
    )
    model_used: Mapped[str] = mapped_column(String(120), nullable=False)
    purpose: Mapped[AiPurpose] = mapped_column(
        Enum(AiPurpose, name="ai_request_purpose",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False, default=AiPurpose.chat,
    )
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Nullable on purpose: unset pricing yields NULL, never a made-up number.
    cost_estimate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Which chunks fed this answer. NFR-7 requires every AI answer to be
    # traceable to its source knowledge, and DATABASE_DESIGN.md anticipates
    # exactly this column.
    retrieved_chunk_ids: Mapped[list[int] | None] = mapped_column(JSON, nullable=True)
    # Names the provider that failed, when this turn ran on deterministic
    # rules because a model call did not succeed. NULL means the turn ran
    # normally - including when no provider is configured at all, which is a
    # deliberate setting rather than a failure.
    degraded_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Indexed: the dashboard asks "did anything degrade in the last 15
    # minutes" on every 5s poll. Declared here as well as in migration
    # 0006 so the models stay the honest description of the schema.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
        index=True,
    )
