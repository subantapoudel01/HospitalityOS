"""
Greetings and pleasantries must never reach the knowledge base.

The bug this fixes: "hello" scored 0.0145 against the corpus, fell below the
similarity floor, and got the strict "I do not have that information"
refusal. Correct for an unanswerable question, badly wrong for a greeting.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core import model_router
from app.modules.receptionist.models import AiRequest, KnowledgeSourceType, Sender
from app.modules.receptionist.rag import ingest, retrieval
from app.modules.receptionist.services import conversation as convo
from app.modules.receptionist.services import smalltalk

pytestmark = pytest.mark.asyncio

CORPUS = [
    ("Checkin Checkout policy", "policy",
     "Checkin Checkout policy: Check-in is from 2 PM. Check-out is by 11 AM."),
    ("Rooms and rates", "upload",
     "Deluxe Lake View rooms cost NPR 4500 per night and sleep two guests."),
]

GREETINGS = ["hello", "Hi there!", "  HEY  ", "good morning", "namaste", "नमस्ते"]
PLEASANTRIES = ["thanks", "thank you so much", "bye", "dhanyabad", "धन्यवाद"]
CAPABILITY = ["what can you do", "are you a bot", "who are you"]

# Must NOT be treated as small talk: each is a real question.
REAL_QUESTIONS = [
    "hello, what time is check-out?",
    "hi, how much is a deluxe room?",
    "what time is check-out?",
    "thanks, but what about pets?",
]


@pytest.fixture(autouse=True)
def _force_extractive(monkeypatch):
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")


async def _seed(db, hotel):
    for title, st, body in CORPUS:
        await ingest.ingest_text(
            db, hotel_id=hotel.id, title=title, raw_content=body,
            source_type=KnowledgeSourceType(st),
        )
    await db.flush()


@pytest.mark.parametrize("text", GREETINGS + PLEASANTRIES + CAPABILITY)
async def test_pleasantries_never_touch_retrieval(db, hotel, text, monkeypatch):
    """Hard assertion: pgvector is not queried at all for small talk."""
    await _seed(db, hotel)

    async def _explode(*args, **kwargs):  # pragma: no cover - must not run
        raise AssertionError(f"retrieval was called for small talk: {text!r}")

    monkeypatch.setattr(retrieval, "search", _explode)

    turn = await convo.send_message(db, hotel_id=hotel.id, text=text)
    assert turn.intent is convo.ChatIntent.smalltalk
    assert turn.citations == []
    assert turn.model == convo.SMALLTALK_MODEL
    assert turn.top_score is None


@pytest.mark.parametrize("text", GREETINGS)
async def test_greeting_reply_is_welcoming_not_a_refusal(db, hotel, text):
    await _seed(db, hotel)
    turn = await convo.send_message(db, hotel_id=hotel.id, text=text)
    for refusal in convo.REFUSALS.values():
        assert turn.reply != refusal
    assert hotel.name in turn.reply or "स्वागत" in turn.reply


@pytest.mark.parametrize("text", REAL_QUESTIONS)
async def test_real_questions_still_go_to_retrieval(db, hotel, text):
    """A greeting glued to a question is still a question."""
    await _seed(db, hotel)
    turn = await convo.send_message(db, hotel_id=hotel.id, text=text)
    assert turn.intent is not convo.ChatIntent.smalltalk


async def test_smalltalk_is_logged_without_a_model(db, hotel):
    await _seed(db, hotel)
    turn = await convo.send_message(db, hotel_id=hotel.id, text="hello")
    rows = (
        await db.execute(
            select(AiRequest).where(AiRequest.conversation_id == turn.conversation_id)
        )
    ).scalars().all()
    assert len(rows) == 1
    assert rows[0].model_used == convo.SMALLTALK_MODEL
    assert rows[0].cost_estimate == 0.0
    assert rows[0].retrieved_chunk_ids is None


async def test_smalltalk_persists_the_transcript(db, hotel):
    await _seed(db, hotel)
    turn = await convo.send_message(db, hotel_id=hotel.id, text="hello")
    messages = await convo.get_messages(db, turn.conversation_id)
    assert [m.sender for m in messages] == [Sender.guest, Sender.ai]


async def test_nepali_greeting_answered_in_nepali(db, hotel):
    """A Devanagari greeting gets a Devanagari reply, with no model call."""
    await _seed(db, hotel)
    turn = await convo.send_message(db, hotel_id=hotel.id, text="नमस्ते")
    assert turn.language == "ne_devanagari"
    assert any("ऀ" <= ch <= "ॿ" for ch in turn.reply), (
        "expected a Devanagari reply to a Devanagari greeting"
    )


async def test_conversation_continues_after_a_greeting(db, hotel):
    """The greeting must not derail the following real question."""
    await _seed(db, hotel)
    first = await convo.send_message(db, hotel_id=hotel.id, text="hello")
    second = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?",
        conversation_id=first.conversation_id,
    )
    assert second.conversation_id == first.conversation_id
    assert second.intent is convo.ChatIntent.answer
    assert "11 AM" in second.reply


def test_matcher_rejects_long_messages():
    """Length guard: a wordy message is never pleasantry, even if it opens with one."""
    assert smalltalk.match("hello") is not None
    assert smalltalk.match("hello there how much does a deluxe room cost") is None
