"""
Staff dashboard: the queue, status transitions, and the handoff.

No provider needed — none of this touches a model.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.modules.receptionist.models import (
    BookingInquiry,
    Channel,
    Conversation,
    ConversationStatus,
    InquiryStatus,
    Message,
    Sender,
)
from app.modules.receptionist.services import staff
from app.platform.models import Hotel

pytestmark = pytest.mark.asyncio


async def _conversation(db, hotel, *, status=ConversationStatus.active, msgs=()):
    c = Conversation(hotel_id=hotel.id, channel=Channel.website, status=status)
    db.add(c)
    await db.flush()
    for sender, content in msgs:
        db.add(Message(conversation_id=c.id, sender=sender, content=content))
    await db.flush()
    return c


async def _inquiry(db, hotel, *, status=InquiryStatus.new):
    q = BookingInquiry(
        conversation_id=(await _conversation(db, hotel)).id,
        hotel_id=hotel.id,
        check_in_date=date.today() + timedelta(days=3),
        check_out_date=date.today() + timedelta(days=5),
        guest_count=2,
        status=status,
    )
    db.add(q)
    await db.flush()
    return q


# --- queue ---------------------------------------------------------------


async def test_escalated_conversations_sort_first(db, hotel):
    """A guest waiting on a human outranks newer chatter."""
    await _conversation(db, hotel, status=ConversationStatus.active)
    escalated = await _conversation(db, hotel, status=ConversationStatus.escalated)
    await _conversation(db, hotel, status=ConversationStatus.active)

    rows = await staff.list_conversations(db, hotel_id=hotel.id)
    assert rows[0].id == escalated.id, "escalated must be pinned to the top"


async def test_awaiting_staff_until_a_human_actually_replies(db, hotel):
    guest_last = await _conversation(
        db, hotel, status=ConversationStatus.escalated,
        msgs=[(Sender.ai, "hello"), (Sender.guest, "I need a person")],
    )
    # request-human appends an AI acknowledgement, so a freshly escalated
    # conversation ends with an AI message. It is still unanswered.
    ai_last = await _conversation(
        db, hotel, status=ConversationStatus.escalated,
        msgs=[
            (Sender.guest, "I need a person"),
            (Sender.ai, "I have flagged this conversation for a staff member."),
        ],
    )
    answered = await _conversation(
        db, hotel, status=ConversationStatus.escalated,
        msgs=[(Sender.guest, "I need a person"), (Sender.staff, "I am here")],
    )
    rows = {r.id: r for r in await staff.list_conversations(db, hotel_id=hotel.id)}
    assert rows[guest_last.id].awaiting_staff is True
    assert rows[ai_last.id].awaiting_staff is True, (
        "a just-escalated conversation must be flagged, not hidden"
    )
    assert rows[answered.id].awaiting_staff is False


async def test_active_conversation_is_never_awaiting_staff(db, hotel):
    c = await _conversation(
        db, hotel, status=ConversationStatus.active, msgs=[(Sender.guest, "hi")]
    )
    rows = {r.id: r for r in await staff.list_conversations(db, hotel_id=hotel.id)}
    assert rows[c.id].awaiting_staff is False


async def test_summary_carries_counts_and_preview(db, hotel):
    c = await _conversation(
        db, hotel,
        msgs=[(Sender.guest, "first"), (Sender.ai, "second"), (Sender.guest, "third")],
    )
    rows = {r.id: r for r in await staff.list_conversations(db, hotel_id=hotel.id)}
    assert rows[c.id].message_count == 3
    assert rows[c.id].last_message_preview == "third"


async def test_status_filter_narrows_the_queue(db, hotel):
    await _conversation(db, hotel, status=ConversationStatus.active)
    esc = await _conversation(db, hotel, status=ConversationStatus.escalated)
    rows = await staff.list_conversations(
        db, hotel_id=hotel.id, status=ConversationStatus.escalated
    )
    assert [r.id for r in rows] == [esc.id]


async def test_queue_is_scoped_to_the_hotel(db, hotel):
    mine = await _conversation(db, hotel)
    other = Hotel(name="Someone Else's Property")
    db.add(other)
    await db.flush()
    theirs = await _conversation(db, other)

    ids = {r.id for r in await staff.list_conversations(db, hotel_id=hotel.id)}
    assert mine.id in ids
    assert theirs.id not in ids, "another hotel's conversation leaked into the queue"


# --- status transitions --------------------------------------------------


async def test_resolving_records_the_time(db, hotel):
    """resolved_at existed since Slice C and nothing ever wrote it."""
    c = await _conversation(db, hotel, status=ConversationStatus.escalated)
    assert c.resolved_at is None

    updated = await staff.set_conversation_status(
        db, conversation_id=c.id, hotel_id=hotel.id,
        status=ConversationStatus.resolved,
    )
    assert updated.status is ConversationStatus.resolved
    assert updated.resolved_at is not None


async def test_reopening_clears_resolved_at(db, hotel):
    """The column must answer 'when was this closed', not 'last touched'."""
    c = await _conversation(db, hotel)
    await staff.set_conversation_status(
        db, conversation_id=c.id, hotel_id=hotel.id,
        status=ConversationStatus.resolved,
    )
    reopened = await staff.set_conversation_status(
        db, conversation_id=c.id, hotel_id=hotel.id,
        status=ConversationStatus.escalated,
    )
    assert reopened.status is ConversationStatus.escalated
    assert reopened.resolved_at is None


async def test_status_change_is_tenant_scoped(db, hotel):
    c = await _conversation(db, hotel)
    other = Hotel(name="Nosy Neighbour")
    db.add(other)
    await db.flush()
    with pytest.raises(LookupError):
        await staff.set_conversation_status(
            db, conversation_id=c.id, hotel_id=other.id,
            status=ConversationStatus.resolved,
        )


# --- handoff -------------------------------------------------------------


async def test_staff_reply_is_stored_as_staff(db, hotel):
    c = await _conversation(db, hotel, status=ConversationStatus.escalated)
    message = await staff.post_staff_message(
        db, conversation_id=c.id, hotel_id=hotel.id, content="  On my way.  "
    )
    assert message.sender is Sender.staff
    assert message.content == "On my way."


async def test_staff_reply_does_not_silently_resolve(db, hotel):
    """Answering is not the same as settling the matter."""
    c = await _conversation(db, hotel, status=ConversationStatus.escalated)
    await staff.post_staff_message(
        db, conversation_id=c.id, hotel_id=hotel.id, content="Looking into it"
    )
    refreshed = (
        await db.execute(select(Conversation).where(Conversation.id == c.id))
    ).scalar_one()
    assert refreshed.status is ConversationStatus.escalated


async def test_staff_reply_is_tenant_scoped(db, hotel):
    c = await _conversation(db, hotel)
    other = Hotel(name="Nosy Neighbour")
    db.add(other)
    await db.flush()
    with pytest.raises(LookupError):
        await staff.post_staff_message(
            db, conversation_id=c.id, hotel_id=other.id, content="hello"
        )


# --- inquiries -----------------------------------------------------------


async def test_inquiry_status_can_be_advanced(db, hotel):
    q = await _inquiry(db, hotel)
    updated = await staff.set_inquiry_status(
        db, inquiry_id=q.id, hotel_id=hotel.id, status=InquiryStatus.contacted
    )
    assert updated.status is InquiryStatus.contacted


async def test_inquiry_status_is_tenant_scoped(db, hotel):
    q = await _inquiry(db, hotel)
    other = Hotel(name="Nosy Neighbour")
    db.add(other)
    await db.flush()
    with pytest.raises(LookupError):
        await staff.set_inquiry_status(
            db, inquiry_id=q.id, hotel_id=other.id, status=InquiryStatus.lost
        )


async def test_inquiry_filter_and_scoping(db, hotel):
    new = await _inquiry(db, hotel, status=InquiryStatus.new)
    await _inquiry(db, hotel, status=InquiryStatus.lost)
    rows = await staff.list_inquiries(
        db, hotel_id=hotel.id, status=InquiryStatus.new
    )
    assert [r.id for r in rows] == [new.id]


# --- metrics -------------------------------------------------------------


async def test_metrics_count_only_this_hotel(db, hotel):
    await _conversation(db, hotel, status=ConversationStatus.escalated)
    await _conversation(db, hotel, status=ConversationStatus.resolved)
    await _conversation(db, hotel, status=ConversationStatus.active)
    await _inquiry(db, hotel, status=InquiryStatus.new)

    other = Hotel(name="Someone Else's Property")
    db.add(other)
    await db.flush()
    await _conversation(db, other, status=ConversationStatus.escalated)

    m = await staff.metrics(db, hotel_id=hotel.id)
    assert m.escalated == 1, "counted another hotel's escalation"
    assert m.resolved == 1
    assert m.inquiries_new == 1
    # 3 above plus the one created inside _inquiry
    assert m.conversations_total == 4
