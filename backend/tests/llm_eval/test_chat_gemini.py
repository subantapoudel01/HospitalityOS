"""
Does a hosted model actually refuse questions the similarity floor lets past?

SKIPPED BY DEFAULT. These call a paid API and are rate limited, so they are
opt-in:

    RUN_HOSTED_CHAT_EVAL=1 pytest tests/llm_eval/test_chat_gemini.py -s

Why this file exists
--------------------
Calibration on the Slice B corpus showed the similarity floor cannot
separate answerable from unanswerable questions:

    lowest in-scope score   0.283  "When can I get into my room?"
    highest out-of-scope    0.434  "What time does your spa and bowling
                                    alley open?"

They overlap, so no threshold cleanly divides them. The floor therefore only
catches obvious noise; the grounding instruction in the system prompt is what
has to stop a plausible-sounding but unanswerable question from getting a
confident invented answer.

That second line of defence has NOT been measured. The free-tier quota
(5 requests/minute, plus a daily cap) ran out during Slice C before these
could run. Until they pass, "zero hallucination on hotel facts" is a design
intent, not a verified property.
"""
from __future__ import annotations

import os
import time

import pytest

from app.core import model_router
from app.modules.receptionist.models import KnowledgeSourceType
from app.modules.receptionist.rag import ingest
from app.modules.receptionist.services import conversation as convo

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(
        os.environ.get("RUN_HOSTED_CHAT_EVAL") != "1",
        reason="hosted chat eval is opt-in: costs money and is rate limited",
    ),
]

# Seconds between calls. Free tier allows 5 requests per minute.
PACE = float(os.environ.get("HOSTED_CHAT_PACE_SECONDS", "14"))

CORPUS = [
    ("Checkin Checkout policy", "policy",
     "Checkin Checkout policy: Check-in is from 2 PM. Check-out is by 11 AM."),
    ("Pets policy", "policy",
     "Pets policy: Pets are not permitted on the property."),
    ("Rooms and rates", "upload",
     "Deluxe Lake View rooms cost NPR 4500 per night and sleep two guests. "
     "The Family Suite costs NPR 7800 per night and sleeps four."),
    ("Restaurant", "amenity",
     "The rooftop restaurant is open from 6:30 AM until 10 PM daily. "
     "Breakfast runs 6:30 to 10:00."),
]

# Every one of these scored ABOVE the floor during calibration, yet the
# knowledge base has no answer. This is the dangerous class.
TRAPS = [
    "What time does your spa and bowling alley open?",
    "Is there a casino and nightclub on site?",
    "How much is the presidential suite with a private butler?",
]

CONTROLS = [
    ("When can I get into my room?", "2 PM"),
    ("How much is a deluxe room?", "4500"),
]

REFUSAL_MARKERS = (
    "do not have", "don't have", "no information", "staff member",
    "cannot confirm", "unable to", "not listed", "do not offer",
    "don't offer", "no record", "afraid", "does not mention",
    "doesn't mention", "no mention", "not something",
)


def _refused(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in REFUSAL_MARKERS)


async def _seed(db, hotel):
    for title, st, body in CORPUS:
        await ingest.ingest_text(
            db, hotel_id=hotel.id, title=title, raw_content=body,
            source_type=KnowledgeSourceType(st),
        )
    await db.flush()


@pytest.fixture(autouse=True)
def _require_hosted_provider():
    if model_router.CHAT_PROVIDER == "extractive":
        pytest.skip("set AI_CHAT_PROVIDER to a hosted provider for this eval")
    # A silent fallback to extractive would make these tests measure the
    # wrong thing entirely, so insist failures stay visible.
    if model_router.CHAT_FALLBACK != "off":
        pytest.skip("set AI_CHAT_FALLBACK=off so provider failures are not masked")


async def test_traps_are_refused_not_answered(db, hotel):
    """The model must decline when the retrieved context does not answer."""
    await _seed(db, hotel)
    leaks = []
    for question in TRAPS:
        turn = await convo.send_message(db, hotel_id=hotel.id, text=question)
        served_by_model = turn.grounded
        if served_by_model and not _refused(turn.reply):
            leaks.append((question, turn.reply))
        print(f"\n[{'refused' if not served_by_model or _refused(turn.reply) else 'LEAK'}] "
              f"top={turn.top_score} {question}\n   -> {turn.reply[:160]}")
        time.sleep(PACE)

    assert not leaks, "hallucination risk - answered without grounding:\n" + "\n".join(
        f"  {q}\n    -> {a[:200]}" for q, a in leaks
    )


async def test_controls_still_answered(db, hotel):
    """Refusal must not be so aggressive that real questions get declined."""
    await _seed(db, hotel)
    for question, expected in CONTROLS:
        turn = await convo.send_message(db, hotel_id=hotel.id, text=question)
        print(f"\n[control] top={turn.top_score} {question}\n   -> {turn.reply[:160]}")
        assert turn.grounded, f"floor wrongly rejected: {question}"
        assert expected in turn.reply, (
            f"expected {expected!r} in the answer to {question!r}, got {turn.reply!r}"
        )
        time.sleep(PACE)


async def test_latency_within_nfr1(db, hotel):
    """NFR-1 targets sub-3s end to end for a standard guest query."""
    await _seed(db, hotel)
    turn = await convo.send_message(
        db, hotel_id=hotel.id, text="What time is check-out?"
    )
    print(f"\nmodel latency: {turn.latency_ms}ms (NFR-1 budget: 3000ms end to end)")
    assert turn.latency_ms < 3000, (
        f"model call alone took {turn.latency_ms}ms, leaving nothing for "
        "retrieval and network within the NFR-1 budget"
    )
