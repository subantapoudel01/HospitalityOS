"""
Staff-side operations: the queue, the transcript, and the handoff.

Everything here is tenant-scoped by hotel_id at the query level, not by the
caller remembering to filter. The dashboard exposes complete guest
transcripts, so a missing WHERE clause is a data breach rather than a bug.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.receptionist.models import (
    AiRequest,
    BookingInquiry,
    Conversation,
    ConversationStatus,
    InquiryStatus,
    Message,
    Sender,
)


@dataclass
class ConversationSummary:
    id: int
    status: ConversationStatus
    channel: str
    started_at: datetime
    resolved_at: datetime | None
    message_count: int
    last_message_at: datetime | None
    last_message_preview: str | None
    awaiting_staff: bool
    # Why it was escalated (US-4). None means the guest asked for a human
    # themselves, which reads differently to staff than the AI deciding
    # the conversation was failing.
    escalation_trigger: str | None = None
    escalation_reason: str | None = None


@dataclass
class Metrics:
    conversations_total: int
    escalated: int
    resolved: int
    inquiries_new: int


async def metrics(db: AsyncSession, *, hotel_id: int) -> Metrics:
    """Four counts for the dashboard header (the useful half of US-9)."""
    convo_counts = (
        await db.execute(
            select(
                func.count(Conversation.id),
                func.count(
                    case((Conversation.status == ConversationStatus.escalated, 1))
                ),
                func.count(
                    case((Conversation.status == ConversationStatus.resolved, 1))
                ),
            ).where(Conversation.hotel_id == hotel_id)
        )
    ).one()

    new_inquiries = (
        await db.execute(
            select(func.count(BookingInquiry.id)).where(
                BookingInquiry.hotel_id == hotel_id,
                BookingInquiry.status == InquiryStatus.new,
            )
        )
    ).scalar_one()

    return Metrics(
        conversations_total=convo_counts[0],
        escalated=convo_counts[1],
        resolved=convo_counts[2],
        inquiries_new=new_inquiries,
    )


async def list_conversations(
    db: AsyncSession,
    *,
    hotel_id: int,
    status: ConversationStatus | None = None,
    limit: int = 100,
) -> list[ConversationSummary]:
    """The staff queue.

    Escalated conversations sort first regardless of age: a guest waiting on
    a human is the highest-stakes item on the screen, and burying it under
    newer chatter is how a lead is lost (UI_UX_PLAN).
    """
    last_message = (
        select(
            Message.conversation_id.label("cid"),
            func.max(Message.id).label("last_id"),
            func.count(Message.id).label("n"),
        )
        .group_by(Message.conversation_id)
        .subquery()
    )

    stmt = (
        select(
            Conversation,
            func.coalesce(last_message.c.n, 0),
            Message.content,
            Message.created_at,
            Message.sender,
        )
        .outerjoin(last_message, last_message.c.cid == Conversation.id)
        .outerjoin(Message, Message.id == last_message.c.last_id)
        .where(Conversation.hotel_id == hotel_id)
        .order_by(
            # escalated (0) before everything else (1), then newest activity
            case((Conversation.status == ConversationStatus.escalated, 0), else_=1),
            Conversation.id.desc(),
        )
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(Conversation.status == status)

    rows = (await db.execute(stmt)).all()
    out: list[ConversationSummary] = []
    for conversation, count, content, created_at, sender in rows:
        out.append(
            ConversationSummary(
                id=conversation.id,
                status=conversation.status,
                channel=conversation.channel.value,
                started_at=conversation.started_at,
                resolved_at=conversation.resolved_at,
                message_count=count,
                last_message_at=created_at,
                last_message_preview=(content or "")[:140] or None,
                # Escalated and no human has spoken last. Checking for
                # "guest spoke last" was wrong: request-human appends an AI
                # message ("I have flagged this for a staff member"), so a
                # freshly escalated conversation had an AI last message and
                # was never flagged - exactly when staff most need it.
                awaiting_staff=(
                    conversation.status is ConversationStatus.escalated
                    and sender is not Sender.staff
                ),
                escalation_trigger=conversation.escalation_trigger,
                escalation_reason=conversation.escalation_reason,
            )
        )
    return out


async def set_conversation_status(
    db: AsyncSession,
    *,
    conversation_id: int,
    hotel_id: int,
    status: ConversationStatus,
) -> Conversation:
    """Move a conversation through the queue, maintaining resolved_at.

    resolved_at existed since Slice C and was never written by anything;
    this is the first code that owns it. Reopening clears it, so the column
    always answers "when was this closed" rather than "when was it last
    touched".
    """
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.hotel_id == hotel_id,
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise LookupError(f"Conversation {conversation_id} not found")

    conversation.status = status
    if status is ConversationStatus.resolved:
        conversation.resolved_at = datetime.now(timezone.utc)
    else:
        conversation.resolved_at = None

    await db.flush()
    return conversation


async def post_staff_message(
    db: AsyncSession, *, conversation_id: int, hotel_id: int, content: str
) -> Message:
    """A real person replying in the thread.

    Deliberately does not change the status. A staff member answering does
    not automatically mean the matter is settled, and inferring that would
    quietly close conversations that are still in progress.
    """
    conversation = (
        await db.execute(
            select(Conversation).where(
                Conversation.id == conversation_id,
                Conversation.hotel_id == hotel_id,
            )
        )
    ).scalar_one_or_none()
    if conversation is None:
        raise LookupError(f"Conversation {conversation_id} not found")

    message = Message(
        conversation_id=conversation.id,
        sender=Sender.staff,
        content=content.strip(),
        language_detected=None,
    )
    db.add(message)
    await db.flush()
    return message


async def list_inquiries(
    db: AsyncSession,
    *,
    hotel_id: int,
    status: InquiryStatus | None = None,
    limit: int = 200,
) -> list[BookingInquiry]:
    stmt = (
        select(BookingInquiry)
        .where(BookingInquiry.hotel_id == hotel_id)
        .order_by(BookingInquiry.id.desc())
        .limit(limit)
    )
    if status is not None:
        stmt = stmt.where(BookingInquiry.status == status)
    return list((await db.execute(stmt)).scalars().all())


async def set_inquiry_status(
    db: AsyncSession, *, inquiry_id: int, hotel_id: int, status: InquiryStatus
) -> BookingInquiry:
    inquiry = (
        await db.execute(
            select(BookingInquiry).where(
                BookingInquiry.id == inquiry_id,
                BookingInquiry.hotel_id == hotel_id,
            )
        )
    ).scalar_one_or_none()
    if inquiry is None:
        raise LookupError(f"Booking inquiry {inquiry_id} not found")

    inquiry.status = status
    await db.flush()
    return inquiry


@dataclass
class Degradation:
    degraded: bool
    window_minutes: int
    events: int
    last_at: datetime | None
    providers: list[str]
    by_purpose: dict[str, int]


async def degradation(
    db: AsyncSession, *, hotel_id: int, minutes: int = 15
) -> Degradation:
    """Has anything run on rules because a model call failed, recently?

    Any single event counts. During light traffic one degraded turn may be
    the only guest of the hour, and that guest's booking request came back
    as a refusal - waiting for a rate to look bad would hide exactly the
    case this is meant to surface.

    Rows where degraded_from is NULL ran normally, including deliberately
    keyless setups. Only genuine failures are counted.
    """
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)

    rows = (
        await db.execute(
            select(AiRequest)
            .join(Conversation, Conversation.id == AiRequest.conversation_id)
            .where(
                Conversation.hotel_id == hotel_id,
                AiRequest.degraded_from.is_not(None),
                AiRequest.created_at >= since,
            )
            .order_by(AiRequest.created_at.desc())
        )
    ).scalars().all()

    by_purpose: dict[str, int] = {}
    providers: set[str] = set()
    for row in rows:
        key = row.purpose.value if hasattr(row.purpose, "value") else str(row.purpose)
        by_purpose[key] = by_purpose.get(key, 0) + 1
        if row.degraded_from:
            providers.add(row.degraded_from)

    return Degradation(
        degraded=bool(rows),
        window_minutes=minutes,
        events=len(rows),
        last_at=rows[0].created_at if rows else None,
        providers=sorted(providers),
        by_purpose=by_purpose,
    )
