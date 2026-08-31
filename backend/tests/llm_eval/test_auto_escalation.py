"""
Automatic escalation (US-4).

Escalation used to be guest-initiated only: a guest who did not know they
could ask for a person just collected refusals until they left.

MOST OF THIS FILE IS ABOUT NOT FIRING. Every false escalation pulls a real
person into a conversation that did not need one, and staff who are
interrupted for nothing three times stop trusting the queue - at which
point the feature is worse than its absence. The over-trigger guards
matter more than the triggers.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.core import model_router
from app.core.config import settings
from app.modules.receptionist.models import (
    Conversation,
    ConversationStatus,
    KnowledgeSourceType,
    Message,
    Sender,
)
from app.modules.receptionist.rag import ingest
from app.modules.receptionist.services import conversation as convo
from app.modules.receptionist.services import frustration, replies

pytestmark = pytest.mark.asyncio

CORPUS = [
    ("Checkin Checkout policy", "policy",
     "Checkin Checkout policy: Check-in is from 2 PM. Check-out is by 11 AM."),
    ("Breakfast", "amenity",
     "Breakfast is served in the dining room from 7:00 AM to 9:30 AM."),
]


@pytest.fixture(autouse=True)
def _rules_only(monkeypatch):
    """No provider. These are our rules under test, not a model's mood."""
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")
    monkeypatch.setattr(settings, "chat_auto_escalate", True)
    monkeypatch.setattr(settings, "chat_dead_end_turns", 3)


async def _seed(db, hotel):
    for title, st, body in CORPUS:
        await ingest.ingest_text(
            db, hotel_id=hotel.id, title=title, raw_content=body,
            source_type=KnowledgeSourceType(st),
        )
    await db.flush()


async def _reload(db, conversation_id) -> Conversation:
    return (
        await db.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
    ).scalar_one()


# --- trigger 1: the guest says so ---------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "this is useless",
        "you are not helping at all",
        "I already told you, I need the check-in time",
        "this is a waste of time",
        "just answer my question",
    ],
)
async def test_stated_frustration_escalates(db, hotel, text):
    await _seed(db, hotel)
    turn = await convo.send_message(db, hotel_id=hotel.id, text=text)

    assert turn.intent is convo.ChatIntent.escalation
    conversation = await _reload(db, turn.conversation_id)
    assert conversation.status is ConversationStatus.escalated
    assert conversation.escalation_trigger == "frustration"


async def test_the_reason_is_recorded_for_staff(db, hotel):
    """Staff triage the queue before opening transcripts. 'The guest asked
    for a person' and 'the AI decided it was failing' need different
    opening lines."""
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="you are useless"
    )
    conversation = await _reload(db, turn.conversation_id)
    assert conversation.escalation_reason
    assert "not helping" in conversation.escalation_reason.lower()


async def test_a_guest_asking_directly_records_no_auto_reason(db, hotel):
    """NULL trigger is the signal that a human was asked for, not
    inferred. Conflating the two loses the distinction staff act on."""
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="I want to talk to a human"
    )
    conversation = await _reload(db, turn.conversation_id)
    assert conversation.status is ConversationStatus.escalated
    assert conversation.escalation_trigger is None


async def test_frustration_works_without_a_provider(db, hotel, monkeypatch):
    """The detector must not need a model.

    A guest whose questions are failing because the provider is
    rate-limited is exactly the guest who needs a person, and an
    LLM-backed detector is unavailable in precisely that moment.
    """
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="this is useless"
    )
    assert turn.intent is convo.ChatIntent.escalation


# --- trigger 1: NOT firing ----------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "What time is check-out?",
        "hello",
        "Thanks, that is helpful",
        "The umbrella they lent me was useless in that rain",
        "Sorry for the stupid question, what time is breakfast?",
        "No, I meant the other room",
    ],
)
async def test_ordinary_messages_do_not_escalate(db, hotel, text):
    """The guard. 'useless' about an umbrella and 'stupid' about one's own
    question are not complaints about the assistant."""
    await _seed(db, hotel)
    turn = await convo.send_message(db, hotel_id=hotel.id, text=text)

    assert turn.intent is not convo.ChatIntent.escalation
    conversation = await _reload(db, turn.conversation_id)
    assert conversation.status is ConversationStatus.active


async def test_detection_is_phrase_based_not_word_based():
    """Unit-level, because this is the property that keeps false
    positives down and it should fail loudly if someone shortens a
    marker to a bare word."""
    assert frustration.detect_frustration("this is useless") is not None
    assert frustration.detect_frustration("the wifi is useless") is None
    assert frustration.detect_frustration("useless umbrella") is None


async def test_abuse_is_matched_on_word_boundaries():
    """'shit' must not fire on 'shitake', and 'ass' must never be a
    marker at all - 'assistance' is a word guests use."""
    assert frustration.detect_frustration("what the hell, fuck this") is not None
    assert frustration.detect_frustration("do you serve shitake mushrooms") is None
    assert frustration.detect_frustration("I need some assistance") is None


# --- trigger 2: dead ends -----------------------------------------------

UNANSWERABLE = [
    "Do you have a helipad?",
    "Is there a casino on site?",
    "Do you offer scuba diving lessons?",
]


async def test_repeated_dead_ends_escalate_on_the_third(db, hotel):
    await _seed(db, hotel)
    conversation_id = None
    turns = []
    for question in UNANSWERABLE:
        turn = await convo.send_message(
            db, hotel_id=hotel.id, text=question,
            conversation_id=conversation_id,
        )
        conversation_id = turn.conversation_id
        turns.append(turn)

    assert turns[0].intent is convo.ChatIntent.refusal
    assert turns[1].intent is convo.ChatIntent.refusal
    assert turns[2].intent is convo.ChatIntent.escalation, (
        "three dead ends in a row is a pattern, not a coincidence"
    )

    conversation = await _reload(db, conversation_id)
    assert conversation.escalation_trigger == "dead_end"
    assert "knowledge base" in (conversation.escalation_reason or "")


async def test_two_dead_ends_are_not_enough(db, hotel):
    """Two unanswerable questions in a row is ordinary - a guest asking
    about a spa and a casino that do not exist. Escalating there would
    interrupt staff for nothing."""
    await _seed(db, hotel)
    conversation_id = None
    for question in UNANSWERABLE[:2]:
        turn = await convo.send_message(
            db, hotel_id=hotel.id, text=question,
            conversation_id=conversation_id,
        )
        conversation_id = turn.conversation_id

    assert (await _reload(db, conversation_id)).status is ConversationStatus.active


async def test_a_good_answer_resets_the_run(db, hotel):
    """refuse, answer, refuse, refuse must not escalate: the run is
    broken. Counting refusals in the whole conversation instead of
    consecutively would escalate a guest who is being helped fine."""
    await _seed(db, hotel)
    conversation_id = None
    script = [
        "Do you have a helipad?",          # refusal 1
        "What time is check-out?",         # answered - resets
        "Is there a casino on site?",      # refusal 1 again
        "Do you offer scuba diving?",      # refusal 2
    ]
    for text in script:
        turn = await convo.send_message(
            db, hotel_id=hotel.id, text=text, conversation_id=conversation_id
        )
        conversation_id = turn.conversation_id

    assert (await _reload(db, conversation_id)).status is ConversationStatus.active


async def test_the_guest_is_not_refused_and_handed_over_at_once(db, hotel):
    """One message, not two. Saying 'I don't have that information'
    immediately followed by 'I've asked a staff member' is the exact
    clumsiness this feature exists to remove."""
    await _seed(db, hotel)
    conversation_id = None
    for question in UNANSWERABLE:
        turn = await convo.send_message(
            db, hotel_id=hotel.id, text=question,
            conversation_id=conversation_id,
        )
        conversation_id = turn.conversation_id

    messages = await convo.get_messages(db, conversation_id)
    last_ai = [m for m in messages if m.sender is Sender.ai][-1]
    assert last_ai.content == replies.ESCALATION_CONFIRMED[
        convo.Language.en
    ]
    assert last_ai.content not in replies.REFUSALS.values()


# --- interaction with the rest of the pipeline ---------------------------


async def test_answerable_questions_never_escalate(db, hotel):
    await _seed(db, hotel)
    conversation_id = None
    for _ in range(4):
        turn = await convo.send_message(
            db, hotel_id=hotel.id, text="What time is check-out?",
            conversation_id=conversation_id,
        )
        conversation_id = turn.conversation_id

    assert (await _reload(db, conversation_id)).status is ConversationStatus.active


async def test_an_already_escalated_conversation_still_stands_down(db, hotel):
    """The stand-down must win. Re-escalating an escalated conversation
    would reset its reason and hide why a human was called in."""
    await _seed(db, hotel)
    first = await convo.send_message(
        db, hotel_id=hotel.id, text="this is useless"
    )
    second = await convo.send_message(
        db, hotel_id=hotel.id, text="still useless, this is a waste of time",
        conversation_id=first.conversation_id,
    )
    assert second.intent is convo.ChatIntent.stood_down

    conversation = await _reload(db, first.conversation_id)
    assert conversation.escalation_trigger == "frustration"


# --- the off switch ------------------------------------------------------


async def test_the_feature_can_be_turned_off(db, hotel, monkeypatch):
    """A property that would rather never auto-escalate must be able to
    say so without editing code."""
    monkeypatch.setattr(settings, "chat_auto_escalate", False)
    await _seed(db, hotel)

    turn = await convo.send_message(db, hotel_id=hotel.id, text="this is useless")
    assert turn.intent is not convo.ChatIntent.escalation
    assert (await _reload(db, turn.conversation_id)).status is (
        ConversationStatus.active
    )


async def test_the_dead_end_trigger_can_be_disabled_alone(db, hotel, monkeypatch):
    """0 turns off dead-end detection while leaving stated frustration on."""
    monkeypatch.setattr(settings, "chat_dead_end_turns", 0)
    await _seed(db, hotel)

    conversation_id = None
    for question in UNANSWERABLE + ["Do you have a golf course?"]:
        turn = await convo.send_message(
            db, hotel_id=hotel.id, text=question,
            conversation_id=conversation_id,
        )
        conversation_id = turn.conversation_id
    assert (await _reload(db, conversation_id)).status is ConversationStatus.active

    follow_up = await convo.send_message(
        db, hotel_id=hotel.id, text="this is useless",
        conversation_id=conversation_id,
    )
    assert follow_up.intent is convo.ChatIntent.escalation


# --- the hosted-provider path -------------------------------------------
#
# The tests above run on `extractive`, where a dead end comes from the
# similarity floor and never reaches a model. With a hosted provider the
# far more common shape is: retrieval CLEARS the floor, and the model
# correctly declines because the passages do not actually answer.
#
# Measured live against Groq before this was handled: three unanswerable
# questions in a row produced intent=refusal, answer, answer and never
# escalated, because the model paraphrased its refusal and nothing
# recognised it. The prompt now pins the exact wording.


def _decline_with(text: str):
    def _fake_chat(**kwargs):
        return model_router.ChatResult(
            text=text, provider="stub", model="stub", latency_ms=1,
        )
    return _fake_chat


async def test_a_model_decline_is_reported_as_a_refusal(db, hotel, monkeypatch):
    """Not as an answer. Labelling a decline 'answer' is what made the
    golden-set grader mis-score refusals, and it hides them from the
    dashboard just as effectively."""
    await _seed(db, hotel)
    monkeypatch.setattr(
        model_router, "chat",
        _decline_with(replies.REFUSALS[convo.Language.en]),
    )
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?"
    )
    assert turn.intent is convo.ChatIntent.refusal


async def test_model_declines_accumulate_towards_a_dead_end(db, hotel, monkeypatch):
    await _seed(db, hotel)
    monkeypatch.setattr(
        model_router, "chat",
        _decline_with(replies.REFUSALS[convo.Language.en]),
    )
    conversation_id = None
    intents = []
    for _ in range(3):
        turn = await convo.send_message(
            db, hotel_id=hotel.id, text="What time is check-out?",
            conversation_id=conversation_id,
        )
        conversation_id = turn.conversation_id
        intents.append(turn.intent)

    assert intents[:2] == [convo.ChatIntent.refusal, convo.ChatIntent.refusal]
    assert intents[2] is convo.ChatIntent.escalation
    assert (await _reload(db, conversation_id)).escalation_trigger == "dead_end"


async def test_a_real_answer_containing_sorry_is_not_a_refusal(db, hotel, monkeypatch):
    """The reason matching is exact rather than keyword-based. 'I'm
    sorry, the pool closes at 8' is an answer, and treating it as a
    refusal would escalate a guest who was just helped."""
    await _seed(db, hotel)
    monkeypatch.setattr(
        model_router, "chat",
        _decline_with("I'm sorry, but the pool closes at 8 PM."),
    )
    conversation_id = None
    for _ in range(4):
        turn = await convo.send_message(
            db, hotel_id=hotel.id, text="When does the pool close?",
            conversation_id=conversation_id,
        )
        conversation_id = turn.conversation_id
        assert turn.intent is convo.ChatIntent.answer

    assert (await _reload(db, conversation_id)).status is ConversationStatus.active


async def test_the_prompt_asks_for_the_exact_refusal_wording(db, hotel, monkeypatch):
    """A paraphrased refusal reads identically to the guest and is
    invisible to both the dead-end detector and the 'yes, fetch someone'
    logic. Guard the instruction that prevents it."""
    await _seed(db, hotel)
    captured = {}

    def _capture(**kwargs):
        captured["system"] = kwargs.get("system", "")
        return model_router.ChatResult(
            text="Check-out is 11 AM.", provider="stub", model="stub",
            latency_ms=1,
        )

    monkeypatch.setattr(model_router, "chat", _capture)
    await convo.send_message(db, hotel_id=hotel.id, text="What time is check-out?")

    assert replies.REFUSALS[convo.Language.en] in captured["system"]
    assert "do not reword" in captured["system"].lower()
