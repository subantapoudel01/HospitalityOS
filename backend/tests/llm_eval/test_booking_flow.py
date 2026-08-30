"""
Booking collection through the conversation loop.

The model is stubbed here on purpose. These assert the *flow* — that an
incomplete request asks rather than saves, that a complete one persists
exactly what was extracted, and that inquiries stay inside their tenant.
Whether a real model resolves "next weekend" correctly is a separate,
provider-dependent question measured in test_booking_extraction_live.py.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest
from sqlalchemy import select

from app.modules.receptionist.models import BookingInquiry, InquiryStatus
from app.modules.receptionist.services import booking, conversation as convo
from app.modules.receptionist.services import intent as intent_svc
from app.modules.receptionist.services.intent import GuestIntent, IntentResult
from app.modules.receptionist.services.language import Language
from app.platform.models import Hotel

pytestmark = pytest.mark.asyncio

TOMORROW = date.today() + timedelta(days=1)
LATER = date.today() + timedelta(days=4)


@pytest.fixture
def force_booking_intent(monkeypatch):
    """Route every message down the booking branch."""
    monkeypatch.setattr(
        intent_svc,
        "classify",
        lambda *a, **k: IntentResult(GuestIntent.booking_request, method="stub"),
    )


def _stub_extraction(monkeypatch, slots, follow_up=None, problems=None):
    monkeypatch.setattr(
        booking,
        "extract",
        lambda *a, **k: booking.Extraction(
            slots=slots, problems=problems or [], follow_up=follow_up
        ),
    )


async def test_incomplete_request_asks_and_saves_nothing(
    db, hotel, monkeypatch, force_booking_intent
):
    _stub_extraction(
        monkeypatch,
        booking.BookingSlots(guest_count=2),
        follow_up="Which dates were you thinking of?",
    )
    turn = await convo.send_message(db, hotel_id=hotel.id, text="a room for 2")

    assert turn.intent is convo.ChatIntent.booking
    assert turn.booking_inquiry_id is None
    assert "dates" in turn.reply.lower()
    rows = (
        await db.execute(
            select(BookingInquiry).where(BookingInquiry.hotel_id == hotel.id)
        )
    ).scalars().all()
    assert rows == [], "an incomplete request must not create an inquiry"


async def test_complete_request_saves_exactly_what_was_extracted(
    db, hotel, monkeypatch, force_booking_intent
):
    _stub_extraction(
        monkeypatch,
        booking.BookingSlots(
            check_in_date=TOMORROW,
            check_out_date=LATER,
            guest_count=3,
            room_type_preference="lake view",
        ),
    )
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="3 of us, arriving tomorrow for 3 nights"
    )

    assert turn.booking_inquiry_id is not None
    row = (
        await db.execute(
            select(BookingInquiry).where(BookingInquiry.hotel_id == hotel.id)
        )
    ).scalar_one()
    assert row.check_in_date == TOMORROW
    assert row.check_out_date == LATER
    assert row.guest_count == 3
    assert row.room_type_preference == "lake view"
    assert row.status is InquiryStatus.new
    assert row.hotel_id == hotel.id
    # The guest's own words are kept so staff can sanity-check the parse.
    assert row.raw_request == "3 of us, arriving tomorrow for 3 nights"


async def test_confirmation_matches_the_saved_row(
    db, hotel, monkeypatch, force_booking_intent
):
    _stub_extraction(
        monkeypatch,
        booking.BookingSlots(
            check_in_date=date(2026, 9, 12),
            check_out_date=date(2026, 9, 15),
            guest_count=2,
        ),
    )
    turn = await convo.send_message(db, hotel_id=hotel.id, text="book it")
    row = (
        await db.execute(
            select(BookingInquiry).where(BookingInquiry.hotel_id == hotel.id)
        )
    ).scalar_one()

    assert f"{row.check_in_date:%d %b %Y}" in turn.reply
    assert f"{row.check_out_date:%d %b %Y}" in turn.reply
    assert str(row.guest_count) in turn.reply


async def test_rejected_dates_do_not_reach_the_database(
    db, hotel, monkeypatch, force_booking_intent
):
    """Validation clears bad values, so the turn asks instead of saving."""
    slots, problems = booking.validate(
        booking.BookingSlots(
            check_in_date=date(2020, 1, 1),  # in the past
            check_out_date=LATER,
            guest_count=2,
        ),
        today=date.today(),
    )
    _stub_extraction(monkeypatch, slots, problems=problems)

    turn = await convo.send_message(db, hotel_id=hotel.id, text="last January")
    assert turn.booking_inquiry_id is None
    rows = (
        await db.execute(
            select(BookingInquiry).where(BookingInquiry.hotel_id == hotel.id)
        )
    ).scalars().all()
    assert rows == []


async def test_booking_turn_persists_the_transcript(
    db, hotel, monkeypatch, force_booking_intent
):
    _stub_extraction(
        monkeypatch, booking.BookingSlots(guest_count=2), follow_up="Which dates?"
    )
    turn = await convo.send_message(db, hotel_id=hotel.id, text="a room please")
    messages = await convo.get_messages(db, turn.conversation_id)
    assert len(messages) == 2
    assert messages[-1].content == turn.reply


async def test_inquiries_are_scoped_to_their_hotel(
    db, hotel, monkeypatch, force_booking_intent
):
    _stub_extraction(
        monkeypatch,
        booking.BookingSlots(
            check_in_date=TOMORROW, check_out_date=LATER, guest_count=2
        ),
    )
    await convo.send_message(db, hotel_id=hotel.id, text="book it")

    other = Hotel(name="Someone Else's Property")
    db.add(other)
    await db.flush()

    rows = (
        await db.execute(
            select(BookingInquiry).where(BookingInquiry.hotel_id == other.id)
        )
    ).scalars().all()
    assert rows == [], "an inquiry leaked into another hotel"


async def test_no_model_still_asks_rather_than_failing(db, hotel, force_booking_intent):
    """With the fast tier disabled the guest gets a plain question, not an error."""
    turn = await convo.send_message(db, hotel_id=hotel.id, text="I want a room")
    assert turn.intent is convo.ChatIntent.booking
    assert turn.booking_inquiry_id is None
    assert turn.reply.strip()
    assert "check-in date" in turn.reply


def test_fallback_question_names_every_missing_slot():
    text = booking._fallback_follow_up(list(booking.REQUIRED_SLOTS))
    assert "check-in date" in text
    assert "check-out date" in text
    assert "number of guests" in text
