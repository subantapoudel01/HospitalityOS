"""
Automatic escalation (US-4): notice when the AI is failing a guest, and
fetch a human without waiting to be asked.

Until now escalation was guest-initiated only. A guest who does not know
they can ask for a person just keeps getting refusals until they leave,
and nothing anywhere records that it happened.

TWO INDEPENDENT TRIGGERS, because they fail differently:

  1. FRUSTRATION - the guest says so. "this is useless", "you're not
     helping". Fires on that turn.

  2. DEAD END - the AI has refused several times in a row. The guest may
     be perfectly polite about it; the conversation is still going
     nowhere. This is US-4's "complexity" arm, and it is the one that
     catches the quiet guest who would otherwise just close the tab.

DELIBERATELY DETERMINISTIC. No model call:

  * It must work when the provider is rate-limited. A guest whose
    questions are failing because the API is down is precisely the guest
    who needs a human, and an LLM-based detector is unavailable in exactly
    that moment.
  * The cost of a false positive is a real person interrupted for nothing.
    A phrase list is inspectable and adjustable; a classifier's mood is
    not.

The markers below are PHRASES, not words. "useless" alone appears in "the
umbrella they gave me was useless"; "stupid" appears in "sorry for the
stupid question". Matching those would escalate a guest who is not
frustrated at all, and staff who get pulled into three non-conversations
learn to ignore the queue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.modules.receptionist.models import Message, Sender
from app.modules.receptionist.services.language import Language

# --- what the guest said -------------------------------------------------

#: English. Each entry is a phrase a satisfied guest does not type.
_FRUSTRATION_EN = (
    "this is useless",
    "you are useless",
    "you're useless",
    "useless bot",
    "not helping",
    "no help at all",
    "you keep saying",
    "same answer",
    "i already told you",
    "i already said",
    "i have already told you",
    "asked this already",
    "waste of time",
    "wasting my time",
    "this is ridiculous",
    "this is frustrating",
    "so frustrating",
    "fed up",
    "just answer my question",
    "answer my question",
    "you don't understand",
    "you do not understand",
    "stop repeating",
    "terrible service",
    "worst service",
)

#: Romanized Nepali and Devanagari. Same rule - phrases, not words.
_FRUSTRATION_NE = (
    "kaam lagdaina",          # "it is not useful"
    "sahayog gardaina",       # "does not help"
    "bujhdainau",             # "you don't understand"
    "pahile nai bhaneko",     # "I already said"
    "समय बर्बाद",              # wasting time
    "काम लाग्दैन",              # not useful
    "सहयोग गर्दैन",             # does not help
    "बुझ्दैनौ",                 # you don't understand
    "पहिले नै भनेको",           # I already said
)

#: Profanity aimed at the assistant. Short, and matched as whole words so
#: "assistance" does not trip "ass".
_ABUSE = (
    "fuck", "fucking", "shit", "bullshit", "damn it", "dammit",
    "bloody hell", "wtf",
)

_ABUSE_RE = re.compile(
    r"(?<![a-zA-Z])(" + "|".join(re.escape(w) for w in _ABUSE) + r")(?![a-zA-Z])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Signal:
    """Why the conversation is being handed over.

    `reason` is stored and shown to staff, so it has to say something they
    can act on before they open the transcript.
    """

    trigger: str  # "frustration" | "dead_end"
    reason: str


def _normalise(text: str) -> str:
    return " ".join((text or "").lower().split())


def detect_frustration(text: str) -> Signal | None:
    """Trigger 1: the guest has said the AI is failing them."""
    low = _normalise(text)
    if not low:
        return None

    for phrase in _FRUSTRATION_EN + _FRUSTRATION_NE:
        if phrase in low:
            return Signal(
                trigger="frustration",
                reason="The guest said the assistant was not helping.",
            )

    if _ABUSE_RE.search(low):
        return Signal(
            trigger="frustration",
            reason="The guest used strong language.",
        )

    return None


# --- what the assistant has been doing -----------------------------------


def count_trailing_refusals(
    prior: list[Message], refusal_texts: set[str]
) -> int:
    """How many times in a row the AI has just refused.

    Counts backwards from the most recent AI message and stops at the
    first one that was a real answer, so a run is genuinely consecutive:
    refuse, answer, refuse is one, not two.

    Matched against the exact fixed refusal strings rather than by looking
    for words like "sorry". A perfectly good grounded answer can contain
    "I'm sorry, the pool closes at 8" and must not count as a dead end.
    """
    streak = 0
    for message in reversed(prior):
        if message.sender is not Sender.ai:
            continue
        if _normalise(message.content) in refusal_texts:
            streak += 1
        else:
            break
    return streak


def detect_dead_end(
    prior: list[Message],
    *,
    refusal_texts: set[str],
    threshold: int,
    pending_refusal: bool = False,
) -> Signal | None:
    """Trigger 2: the conversation is going nowhere, politely.

    `pending_refusal=True` counts the refusal the caller is ABOUT to send
    but has not written yet. That is what lets the caller escalate
    *instead* of refusing a third time, rather than sending both messages
    and then handing over one turn late.
    """
    if threshold <= 0:
        return None
    streak = count_trailing_refusals(prior, refusal_texts)
    if pending_refusal:
        streak += 1
    if streak < threshold:
        return None
    return Signal(
        trigger="dead_end",
        reason=(
            f"The assistant could not answer {streak} questions in a row. "
            "The information may be missing from the knowledge base."
        ),
    )


def refusal_text_set(refusals: dict[Language, str]) -> set[str]:
    """The fixed refusals, normalised for comparison."""
    return {_normalise(v) for v in refusals.values()}
