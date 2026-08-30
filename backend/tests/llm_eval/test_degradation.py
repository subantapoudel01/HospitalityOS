"""
Degradation visibility.

When a model call fails the system keeps answering on deterministic rules.
That is the right behaviour and it was completely invisible: an afternoon of
rules-routed turns left no row in ai_requests, no signal in the API
response, and nothing on the dashboard. Booking requests came back as
refusals and the only way to notice was reading raw `method` values in a
debug script.

The distinction these tests protect: a call that FAILED is degradation, a
provider that is deliberately not configured is not. If keyless setups
raised the banner it would be permanently on, and staff would stop seeing it.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.core import model_router
from app.modules.receptionist.models import AiRequest, KnowledgeSourceType
from app.modules.receptionist.rag import ingest
from app.modules.receptionist.services import booking, conversation as convo
from app.modules.receptionist.services import intent as intent_svc
from app.modules.receptionist.services import staff

pytestmark = pytest.mark.asyncio

CORPUS = [
    ("Checkin Checkout policy", "policy",
     "Checkin Checkout policy: Check-in is from 2 PM. Check-out is by 11 AM."),
]


async def _seed(db, hotel):
    for title, st, body in CORPUS:
        await ingest.ingest_text(
            db, hotel_id=hotel.id, title=title, raw_content=body,
            source_type=KnowledgeSourceType(st),
        )
    await db.flush()


async def _degraded_rows(db, hotel_id):
    from app.modules.receptionist.models import Conversation

    return (
        await db.execute(
            select(AiRequest)
            .join(Conversation, Conversation.id == AiRequest.conversation_id)
            .where(
                Conversation.hotel_id == hotel_id,
                AiRequest.degraded_from.is_not(None),
            )
        )
    ).scalars().all()


@pytest.fixture
def fast_tier_fails(monkeypatch):
    """Provider is configured and reachable, but every call errors."""
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "groq")
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")

    def _boom(*args, **kwargs):
        raise model_router.ChatError("Error code: 429 - rate_limit_exceeded")

    monkeypatch.setattr(model_router, "_fast_call", _boom)


# --- the distinction that makes the signal trustworthy -------------------


async def test_keyless_operation_is_not_degradation(db, hotel, monkeypatch):
    """Deliberately running without a provider must not raise the alarm."""
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")
    await _seed(db, hotel)

    await convo.send_message(db, hotel_id=hotel.id, text="What time is check-out?")

    assert await _degraded_rows(db, hotel.id) == []
    state = await staff.degradation(db, hotel_id=hotel.id)
    assert state.degraded is False, (
        "a keyless setup would keep this banner permanently on"
    )


async def test_a_failed_call_is_degradation(db, hotel, fast_tier_fails):
    await _seed(db, hotel)
    await convo.send_message(db, hotel_id=hotel.id, text="What time is check-out?")

    rows = await _degraded_rows(db, hotel.id)
    assert rows, "a failed model call must leave a trace"
    assert all(r.degraded_from == "groq" for r in rows)


# --- each path records ---------------------------------------------------


async def test_intent_fallback_is_recorded(db, hotel, fast_tier_fails):
    """This path previously wrote no row at all."""
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="I want to book a room"
    )
    rows = await _degraded_rows(db, hotel.id)
    assert any(r.model_used == convo.RULES_FALLBACK for r in rows)
    assert turn.conversation_id


async def test_translation_fallback_is_recorded(db, hotel, fast_tier_fails):
    """A Nepali question searched untranslated is a materially worse answer."""
    from app.modules.receptionist.models import AiPurpose

    await _seed(db, hotel)
    await convo.send_message(db, hotel_id=hotel.id, text="कोठाको भाडा कति हो?")

    rows = await _degraded_rows(db, hotel.id)
    assert any(r.purpose is AiPurpose.translation for r in rows), (
        "translation failure must be visible; Slice B measured untranslated "
        "Devanagari retrieval at 0.17 against 0.52 translated"
    )


async def test_booking_extraction_fallback_is_recorded(db, hotel, monkeypatch):
    from app.modules.receptionist.services.intent import GuestIntent, IntentResult

    await _seed(db, hotel)
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "groq")
    monkeypatch.setattr(
        intent_svc, "classify",
        lambda *a, **k: IntentResult(GuestIntent.booking_request, method="stub"),
    )
    monkeypatch.setattr(
        booking, "extract",
        lambda *a, **k: booking.Extraction(
            slots=booking.BookingSlots(), follow_up="Which dates?",
            degraded_from="groq",
        ),
    )
    await convo.send_message(db, hotel_id=hotel.id, text="book me a room")
    assert await _degraded_rows(db, hotel.id)


async def test_chat_generation_degradation_is_persisted(db, hotel, monkeypatch):
    """ChatResult.degraded_from existed since Slice C and was discarded."""
    await _seed(db, hotel)
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")

    real_chat = model_router.chat

    def _degraded_chat(**kwargs):
        result = real_chat(**kwargs)
        result.degraded_from = "groq"
        return result

    monkeypatch.setattr(model_router, "chat", _degraded_chat)
    await convo.send_message(db, hotel_id=hotel.id, text="What time is check-out?")

    rows = await _degraded_rows(db, hotel.id)
    assert any(r.degraded_from == "groq" for r in rows)


# --- the query behind the banner -----------------------------------------


async def test_degradation_summary_counts_and_groups(db, hotel, fast_tier_fails):
    await _seed(db, hotel)
    await convo.send_message(db, hotel_id=hotel.id, text="I want to book a room")
    await convo.send_message(db, hotel_id=hotel.id, text="कोठाको भाडा कति हो?")

    state = await staff.degradation(db, hotel_id=hotel.id)
    assert state.degraded is True
    assert state.events >= 2
    assert state.providers == ["groq"]
    assert state.by_purpose
    assert state.last_at is not None


async def test_old_degradation_falls_out_of_the_window(db, hotel, fast_tier_fails):
    await _seed(db, hotel)
    turn = await convo.send_message(db, hotel_id=hotel.id, text="book a room")

    for row in await _degraded_rows(db, hotel.id):
        row.created_at = datetime.now(timezone.utc) - timedelta(hours=3)
    await db.flush()

    assert (await staff.degradation(db, hotel_id=hotel.id, minutes=15)).degraded is False
    assert (await staff.degradation(db, hotel_id=hotel.id, minutes=600)).degraded is True
    assert turn.conversation_id


async def test_degradation_is_scoped_to_the_hotel(db, hotel, fast_tier_fails):
    from app.platform.models import Hotel

    await _seed(db, hotel)
    await convo.send_message(db, hotel_id=hotel.id, text="book a room")

    other = Hotel(name="Someone Else's Property")
    db.add(other)
    await db.flush()

    assert (await staff.degradation(db, hotel_id=other.id)).degraded is False
    assert (await staff.degradation(db, hotel_id=hotel.id)).degraded is True


async def test_healthy_system_reports_nothing(db, hotel, monkeypatch):
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")
    await _seed(db, hotel)
    await convo.send_message(db, hotel_id=hotel.id, text="hello")

    state = await staff.degradation(db, hotel_id=hotel.id)
    assert state.degraded is False
    assert state.events == 0
    assert state.last_at is None
    assert state.by_purpose == {}
