"""
Escalation intent: asking for a human must actually fetch one.

The bug this covers, reproduced live before the fix:

  AI    : "...Would you like me to pass this to a staff member?"
  Guest : "yes"
  AI    : "Sure, I'll forward your request to our team."   <- forwarded nothing
  Guest : "contact real person"
  AI    : (the same refusal, verbatim)

Two separate failures. The loop, and worse, a promise of human contact that
never happened: the conversation stayed `active` with awaiting_staff False.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core import model_router
from app.modules.receptionist.models import (
    Conversation,
    ConversationStatus,
    KnowledgeSourceType,
    Message,
    Sender,
)
from app.modules.receptionist.rag import ingest, retrieval
from app.modules.receptionist.services import conversation as convo
from app.modules.receptionist.services import intent as intent_svc
from app.modules.receptionist.services import replies
from app.modules.receptionist.services.intent import GuestIntent, IntentResult
from app.modules.receptionist.services.language import Language

pytestmark = pytest.mark.asyncio

CORPUS = [
    ("Checkin Checkout policy", "policy",
     "Checkin Checkout policy: Check-in is from 2 PM. Check-out is by 11 AM."),
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


async def _status(db, conversation_id) -> ConversationStatus:
    return (
        await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
    ).scalar_one().status


# --- explicit requests ---------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "contact real person",
        "I want to talk to a human",
        "can I please speak to someone",
        "get me a manager",
    ],
)
async def test_explicit_request_escalates(db, hotel, text):
    await _seed(db, hotel)
    turn = await convo.send_message(db, hotel_id=hotel.id, text=text)

    assert turn.intent is convo.ChatIntent.escalation
    assert await _status(db, turn.conversation_id) is ConversationStatus.escalated
    assert turn.reply == replies.ESCALATION_CONFIRMED[Language.en]


async def test_explicit_request_never_returns_the_refusal(db, hotel):
    """The exact loop that was reported."""
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="contact real person"
    )
    for refusal in replies.REFUSALS.values():
        assert turn.reply != refusal


# --- "yes" to a handoff offer -------------------------------------------


async def test_yes_after_a_handoff_offer_escalates(db, hotel):
    await _seed(db, hotel)
    first = await convo.send_message(
        db, hotel_id=hotel.id, text="Do you have a helipad and a private yacht?"
    )
    assert first.intent is convo.ChatIntent.refusal

    second = await convo.send_message(
        db, hotel_id=hotel.id, text="yes",
        conversation_id=first.conversation_id,
    )
    assert second.intent is convo.ChatIntent.escalation
    assert await _status(db, first.conversation_id) is ConversationStatus.escalated


async def test_yes_to_something_else_does_not_escalate(db, hotel, monkeypatch):
    """The guard against over-triggering: 'yes' is not always a handoff."""
    await _seed(db, hotel)
    conversation = Conversation(hotel_id=hotel.id)
    db.add(conversation)
    await db.flush()
    db.add(
        Message(
            conversation_id=conversation.id, sender=Sender.ai,
            content="Would you like a lake view room?",
        )
    )
    await db.flush()

    # Rules path only, so this asserts our logic rather than the model's.
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="yes", conversation_id=conversation.id
    )
    assert turn.intent is not convo.ChatIntent.escalation
    assert await _status(db, conversation.id) is ConversationStatus.active


# --- the confirmation is not generated ----------------------------------


async def test_confirmation_is_fixed_text_not_model_output(db, hotel, monkeypatch):
    """A model must never be the one claiming staff were notified.

    Even if the classifier returns prose alongside the escalation intent,
    the guest sees the fixed confirmation that is only emitted after the
    status actually changed.
    """
    await _seed(db, hotel)
    monkeypatch.setattr(
        intent_svc,
        "classify",
        lambda *a, **k: IntentResult(
            GuestIntent.escalation,
            reply="Sure, I'll forward your request to our team.",
            method="stub",
        ),
    )
    turn = await convo.send_message(db, hotel_id=hotel.id, text="get a human")
    assert turn.reply == replies.ESCALATION_CONFIRMED[Language.en]
    assert "forward your request" not in turn.reply


async def test_chat_escalation_matches_the_button(db, hotel, monkeypatch):
    """Both paths must leave the guest in the same place."""
    await _seed(db, hotel)
    monkeypatch.setattr(
        intent_svc,
        "classify",
        lambda *a, **k: IntentResult(GuestIntent.escalation, method="stub"),
    )
    via_chat = await convo.send_message(db, hotel_id=hotel.id, text="human please")

    other = await convo.send_message(db, hotel_id=hotel.id, text="hello")
    await convo.request_human(
        db, conversation_id=other.conversation_id, hotel_id=hotel.id
    )
    button_messages = await convo.get_messages(db, other.conversation_id)

    assert via_chat.reply == button_messages[-1].content


# --- language ------------------------------------------------------------


async def test_confirmation_uses_the_guest_language(db, hotel, monkeypatch):
    await _seed(db, hotel)
    monkeypatch.setattr(
        intent_svc,
        "classify",
        lambda *a, **k: IntentResult(GuestIntent.escalation, method="stub"),
    )
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="कृपया मान्छे बोलाउनुहोस्"
    )
    assert turn.language == "ne_devanagari"
    assert turn.reply == replies.ESCALATION_CONFIRMED[Language.ne_devanagari]


async def test_button_confirmation_also_uses_the_guest_language(db, hotel):
    """request_human replied in English regardless before this."""
    await _seed(db, hotel)
    first = await convo.send_message(db, hotel_id=hotel.id, text="नमस्ते")
    await convo.request_human(
        db, conversation_id=first.conversation_id, hotel_id=hotel.id
    )
    messages = await convo.get_messages(db, first.conversation_id)
    assert messages[-1].content == replies.ESCALATION_CONFIRMED[
        Language.ne_devanagari
    ]


# --- stand-down ----------------------------------------------------------


async def test_ai_stands_down_after_escalation(db, hotel, monkeypatch):
    """Once a human is coming, the AI must not keep answering over them."""
    await _seed(db, hotel)
    first = await convo.send_message(db, hotel_id=hotel.id, text="contact real person")
    assert first.intent is convo.ChatIntent.escalation

    async def _explode(*a, **k):  # pragma: no cover - must not run
        raise AssertionError("retrieval ran on an escalated conversation")

    monkeypatch.setattr(retrieval, "search", _explode)

    follow_up = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?",
        conversation_id=first.conversation_id,
    )
    assert follow_up.intent is convo.ChatIntent.stood_down
    assert follow_up.reply == replies.STAND_DOWN[Language.en]


async def test_stood_down_messages_are_still_recorded_for_staff(db, hotel):
    await _seed(db, hotel)
    first = await convo.send_message(db, hotel_id=hotel.id, text="contact real person")
    await convo.send_message(
        db, hotel_id=hotel.id, text="my booking reference is ABC123",
        conversation_id=first.conversation_id,
    )
    messages = await convo.get_messages(db, first.conversation_id)
    assert any("ABC123" in m.content for m in messages if m.sender is Sender.guest)


async def test_ai_resumes_once_staff_resolve_it(db, hotel):
    await _seed(db, hotel)
    first = await convo.send_message(db, hotel_id=hotel.id, text="contact real person")

    conversation = (
        await db.execute(
            select(Conversation).where(Conversation.id == first.conversation_id)
        )
    ).scalar_one()
    conversation.status = ConversationStatus.resolved
    await db.flush()

    resumed = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?",
        conversation_id=first.conversation_id,
    )
    assert resumed.intent is not convo.ChatIntent.stood_down
    assert "11 AM" in resumed.reply


# --- handoff-offer detection --------------------------------------------


def test_a_knowledge_answer_mentioning_staff_is_not_an_offer():
    """Guards against 'yes' escalating after any answer containing 'staff'."""
    assert not replies.is_handoff_offer(
        "Check-out is by 11 AM. Our staff can help with a late checkout."
    )


@pytest.mark.parametrize("refusal", list(replies.REFUSALS.values()))
def test_every_refusal_counts_as_a_handoff_offer(refusal):
    assert replies.is_handoff_offer(refusal)


def test_extractive_tail_counts_as_a_handoff_offer():
    assert replies.is_handoff_offer(
        "Here is the closest information I have from the hotel:\n\n"
        "- Check-out is by 11 AM.\n\n" + replies.EXTRACTIVE_HANDOFF_TAIL
    )


# --- regression: questions ABOUT the assistant must not escalate ---------
#
# Measured in the pre-pilot test pass: "are you a real person?" summoned a
# staff member. A bare "real person" substring was in the marker list, so a
# guest asking whether they were talking to a bot got escalated instead of
# told - and never received the AI disclosure that question exists to give.


@pytest.mark.parametrize(
    "text",
    [
        "are you a real person?",
        "am I talking to a real person",
        "are you a human?",
        "are you a bot?",
        "is this a robot?",
        "who am I talking to?",
    ],
)
async def test_questions_about_the_assistant_do_not_escalate(db, hotel, text, monkeypatch):
    await _seed(db, hotel)
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")  # rules path
    turn = await convo.send_message(db, hotel_id=hotel.id, text=text)
    assert turn.intent is not convo.ChatIntent.escalation, (
        f"{text!r} asks what I am; it does not ask for a human"
    )
    assert await _status(db, turn.conversation_id) is ConversationStatus.active


@pytest.mark.parametrize(
    "text",
    [
        "I want to talk to a human",
        "can I speak to a real person please",
        "contact real person",
        "get me a manager",
        "connect me to staff",
        "put me through to a person",
    ],
)
async def test_genuine_requests_still_escalate(db, hotel, text, monkeypatch):
    await _seed(db, hotel)
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")
    turn = await convo.send_message(db, hotel_id=hotel.id, text=text)
    assert turn.intent is convo.ChatIntent.escalation
