"""
Conversation loop tests (Slice C).

Pinned to the extractive provider on purpose. These assert the *loop* —
grounding gate, persistence, telemetry, tenant isolation — and those must be
deterministic and free to run in CI. Whether a hosted model phrases a
refusal well is a separate, paid question; see test_chat_gemini.py.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core import model_router
from app.core.config import settings
from app.modules.receptionist.models import (
    AiRequest,
    Conversation,
    ConversationStatus,
    KnowledgeSourceType,
    Message,
    Sender,
)
from app.modules.receptionist.rag import ingest
from app.modules.receptionist.services import conversation as convo
from app.modules.receptionist.services.language import Language
from app.platform.models import Hotel

pytestmark = pytest.mark.asyncio

CORPUS = [
    ("Checkin Checkout policy", "policy",
     "Checkin Checkout policy: Check-in is from 2 PM. Check-out is by 11 AM."),
    ("Pets policy", "policy",
     "Pets policy: Pets are not permitted on the property."),
    ("Rooms and rates", "upload",
     "Deluxe Lake View rooms cost NPR 4500 per night and sleep two guests."),
]


@pytest.fixture(autouse=True)
def _force_extractive(monkeypatch):
    """Never let a developer's .env turn CI into a billed API run."""
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")


async def _seed(db, hotel):
    for title, st, body in CORPUS:
        await ingest.ingest_text(
            db, hotel_id=hotel.id, title=title, raw_content=body,
            source_type=KnowledgeSourceType(st),
        )
    await db.flush()


async def test_grounded_question_answers_and_cites(db, hotel):
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?"
    )
    assert turn.grounded is True
    assert turn.citations, "a grounded answer must cite its sources"
    assert any("Checkin" in c.document_title for c in turn.citations)
    assert "11 AM" in turn.reply


async def test_unanswerable_question_refuses_without_calling_a_model(db, hotel):
    """The floor must reject before generation, not after."""
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="What is the capital of France?"
    )
    assert turn.grounded is False
    assert turn.citations == []
    assert turn.reply == convo.REFUSALS[Language.en]
    assert turn.model == convo.NO_MODEL


async def test_refusal_is_recorded_as_a_non_model_call(db, hotel):
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="Write me a poem about quantum physics"
    )
    assert turn.grounded is False
    rows = (
        await db.execute(
            select(AiRequest).where(AiRequest.conversation_id == turn.conversation_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].model_used == convo.NO_MODEL
    assert rows[0].cost_estimate == 0.0


async def test_transcript_persists_both_sides(db, hotel):
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?"
    )
    messages = await convo.get_messages(db, turn.conversation_id)
    assert [m.sender for m in messages] == [Sender.guest, Sender.ai]
    assert messages[0].content == "What time is check-out?"
    assert all(m.language_detected == "en" for m in messages)


async def test_ai_request_records_source_chunks(db, hotel):
    """NFR-7: an answer must be traceable to the chunks that produced it."""
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="Can I bring my dog?"
    )
    row = (
        await db.execute(
            select(AiRequest).where(AiRequest.conversation_id == turn.conversation_id)
        )
    ).scalar_one()
    assert row.retrieved_chunk_ids, "no provenance recorded"
    assert set(row.retrieved_chunk_ids) == {c.chunk_id for c in turn.citations}
    assert row.latency_ms is not None


async def test_conversation_continues_across_turns(db, hotel):
    await _seed(db, hotel)
    first = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?"
    )
    second = await convo.send_message(
        db, hotel_id=hotel.id, text="And check-in?",
        conversation_id=first.conversation_id,
    )
    assert second.conversation_id == first.conversation_id
    messages = await convo.get_messages(db, first.conversation_id)
    assert len(messages) == 4


async def test_cannot_continue_another_hotels_conversation(db, hotel):
    """Guessing a conversation id must not cross the tenant boundary."""
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?"
    )
    intruder = Hotel(name="Someone Else's Property")
    db.add(intruder)
    await db.flush()

    with pytest.raises(LookupError):
        await convo.send_message(
            db, hotel_id=intruder.id, text="What time is check-out?",
            conversation_id=turn.conversation_id,
        )


async def test_answers_never_draw_on_another_hotels_knowledge(db, hotel):
    await _seed(db, hotel)
    empty = Hotel(name="Empty Property")
    db.add(empty)
    await db.flush()

    turn = await convo.send_message(
        db, hotel_id=empty.id, text="What time is check-out?"
    )
    assert turn.grounded is False, "a hotel with no knowledge base must refuse"
    assert turn.citations == []


async def test_request_human_escalates(db, hotel):
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?"
    )
    conversation = await convo.request_human(
        db, conversation_id=turn.conversation_id, hotel_id=hotel.id
    )
    assert conversation.status is ConversationStatus.escalated
    messages = await convo.get_messages(db, turn.conversation_id)
    assert "staff member" in messages[-1].content


async def test_request_human_is_tenant_scoped(db, hotel):
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?"
    )
    intruder = Hotel(name="Nosy Neighbour")
    db.add(intruder)
    await db.flush()
    with pytest.raises(LookupError):
        await convo.request_human(
            db, conversation_id=turn.conversation_id, hotel_id=intruder.id
        )


async def test_unknown_hotel_is_rejected(db):
    with pytest.raises(LookupError):
        await convo.send_message(db, hotel_id=999999, text="hello")


async def test_floor_is_actually_applied(db, hotel):
    """Guards the setting itself: a floor of 1.0 must refuse everything."""
    await _seed(db, hotel)
    original = settings.chat_min_score
    settings.chat_min_score = 1.0
    try:
        turn = await convo.send_message(
            db, hotel_id=hotel.id, text="What time is check-out?"
        )
        assert turn.grounded is False
    finally:
        settings.chat_min_score = original
