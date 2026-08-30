"""
What is the guest actually asking for?

This runs BEFORE retrieval, because the answer decides whether retrieval
should happen at all. Three outcomes:

    smalltalk        greetings, thanks, "how are you", "what is your name"
                     -> the model writes the reply here and now. No vector
                        search, no knowledge base, no refusal.
    booking_request  the guest wants to reserve or enquire about staying
                     -> slot filling (Slice E)
    hotel_query      anything factual about the property
                     -> retrieval, grounding floor, grounded answer

The earlier deterministic phrase list could only recognise pleasantries it
had been told about, so "how are you" and "what is your name" fell through
to retrieval, scored near zero and got the strict refusal. A guest asking a
polite question does not deserve to be told the knowledge base has no entry
for it.

The rules module survives as a fallback. When the provider is down or no key
is configured, a greeting still gets a greeting rather than an error - the
widget degrades, it does not break.

Failure defaults to `hotel_query`. That direction matters: misrouting small
talk into retrieval produces an awkward refusal, while misrouting a real
question into small talk produces a confidently wrong answer with no
grounding at all.
"""
from __future__ import annotations

import enum
import json
import re
from dataclasses import dataclass

from app.core import model_router
from app.modules.receptionist.services import replies, smalltalk
from app.modules.receptionist.services.language import Language

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)


class GuestIntent(str, enum.Enum):
    smalltalk = "smalltalk"
    hotel_query = "hotel_query"
    booking_request = "booking_request"
    escalation = "escalation"


@dataclass
class IntentResult:
    intent: GuestIntent
    # Only populated for smalltalk: the reply the model already wrote.
    reply: str | None = None
    method: str = "model"  # model | rules | rules-override
    telemetry: model_router.ChatResult | None = None
    # Names the provider that failed, when rules ran because a model call
    # did not succeed. Crucially NOT set when no provider is configured:
    # running keyless is a deliberate setting, and reporting it as
    # degradation would leave a permanent alarm on the dashboard that staff
    # would learn to ignore.
    degraded_from: str | None = None


SYSTEM = """You are the intent classifier for {hotel_name}'s AI receptionist.

Classify the guest's latest message into exactly one intent:

- "smalltalk": greetings, farewells, thanks, apologies, and questions about
  YOU (how are you, what is your name, are you a bot, what can you do).
  Anything social rather than informational.
- "booking_request": the guest wants to reserve, book, check availability,
  or is describing dates and how many people are travelling.
- "hotel_query": any factual question about the property - rooms, rates,
  check-in times, food, policies, facilities, directions, nearby places.
- "escalation": the guest wants a human. Either they ask directly ("talk to
  a person", "contact real staff", "get me a manager", "I want to speak to
  someone"), OR they are agreeing to a handoff you just offered.

About that second case: if YOUR previous message offered to pass the
question to a staff member, then a bare "yes", "ok", "please", "sure",
"hajur" or "ho" is an escalation. If your previous message asked about
anything else - a room type, dates, a preference - the same "yes" is NOT an
escalation. Decide from what was actually asked, never from the word alone.

The same rule governs short replies generally. A bare "yes", "no", a number,
or a date is a CONTINUATION of whatever you last asked, not a fresh topic:
- you asked for booking details -> "booking_request"
- you offered a staff member    -> "escalation"
- you asked a factual follow-up -> "hotel_query"
Never classify a short answer as a new subject just because it is short.

Rules:
- If the message contains a factual hotel question, it is "hotel_query" or
  "booking_request", even if it also contains a greeting.
- Only "smalltalk" gets a reply from you. For every other intent, reply
  must be null - another system handles those.
- NEVER promise to contact, forward, notify or fetch anyone. You cannot do
  it. If the guest wants a human, that is "escalation" and the system sends
  the confirmation itself. Claiming to have forwarded something you have not
  is worse than saying nothing.
- For smalltalk, write a warm, brief reply (one or two sentences) as the
  receptionist for {hotel_name}. Be natural and human. If it fits, invite
  them to ask about rooms, rates, dining or nearby attractions.
- NEVER state a fact about the hotel - no prices, times, policies or
  facilities. You do not have that information. If they ask for a fact, it
  is not smalltalk.
- If the guest asks whether you are human, a bot, an AI, or a real person,
  say plainly that you are an AI assistant for {hotel_name}. Never deflect
  and never imply you are a person. Guests are told up front that this is
  an AI, and being evasive about it destroys the trust that disclosure was
  meant to build.
- {reply_language}

Respond with ONLY a JSON object, no code fences:
{{"intent": "smalltalk" | "booking_request" | "hotel_query" | "escalation",
 "reply": string or null}}"""


_LANGUAGE_HINT = {
    Language.en: "Write the reply in English.",
    Language.ne_romanized: (
        "Write the reply in Romanized Nepali - Nepali in the Latin alphabet, "
        "the way Nepali speakers text. Do not use Devanagari script."
    ),
    Language.ne_devanagari: "Write the reply in Nepali using Devanagari script.",
}


def _parse(raw: str) -> tuple[GuestIntent, str | None]:
    """Pull {intent, reply} out of the model output.

    Models wrap JSON in prose or code fences often enough that grabbing the
    outermost braces is more reliable than trusting the whole string to
    parse.
    """
    match = _JSON_BLOCK.search(raw or "")
    if not match:
        raise ValueError(f"no JSON object in intent response: {raw[:120]!r}")
    data = json.loads(match.group(0))

    intent = GuestIntent(str(data.get("intent", "")).strip().lower())
    reply = data.get("reply")
    if reply is not None:
        reply = str(reply).strip() or None
    if intent is GuestIntent.smalltalk and not reply:
        raise ValueError("smalltalk classified with no reply text")
    return intent, reply


# Explicit asks for a human. Substring markers rather than whole-message
# matching, because "can I please talk to a real person" should count.
#
# Every marker must be REQUEST-shaped. A bare "real person" was here once
# and it made "are you a real person?" summon a staff member: a guest asking
# whether they are talking to a bot got escalated instead of being told, and
# never received the AI disclosure that question deserves. Wanting a human
# and asking what you are talking to are different things.
_HUMAN_MARKERS = (
    "talk to a human", "talk to a person", "talk to a real",
    "talk to someone", "talk to staff", "talk to a manager",
    "speak to a human", "speak to a person", "speak to a real",
    "speak to someone", "speak to staff", "speak to a manager",
    "speak with a human", "speak with a person", "speak with someone",
    "contact real", "contact a human", "contact a person", "contact staff",
    "connect me", "put me through", "get me a human", "get me a person",
    "get me a manager", "get me staff", "want a real person",
    "want to talk to", "want to speak to", "need a human", "need a person",
    "human please", "human agent", "live agent", "real agent",
    "customer service", "manche sanga", "manche lai", "staff sanga",
)

# Questions ABOUT the assistant. These never escalate, even if a marker
# happens to appear inside them - they are answered by small talk, which is
# where the "I am an AI assistant" disclosure lives.
_ABOUT_THE_ASSISTANT = (
    "are you", "r u", "is this a", "am i talking to", "am i speaking to",
    "am i chatting with", "who am i talking to", "who am i speaking to",
)

# Affirmatives that mean "yes, fetch a human" ONLY when the assistant just
# offered exactly that.
_AFFIRMATIVES = {
    "yes", "yes please", "yes pls", "yeah", "yep", "yup", "ya", "ok",
    "okay", "sure", "please", "please do", "do it", "go ahead", "alright",
    "of course", "definitely", "absolutely", "y",
    "ho", "hajur", "hunchha", "huncha", "thik cha", "hos",
}


def _last_assistant_message(history):
    for turn in reversed(history or []):
        if turn.get("role") == "assistant":
            return turn.get("content") or ""
    return ""


def _looks_like_escalation(text, history=None) -> bool:
    cleaned = smalltalk.normalise(text)
    if not cleaned:
        return False
    # "are you a real person?" asks what I am; it does not ask for a human.
    if cleaned.startswith(_ABOUT_THE_ASSISTANT):
        return False
    if any(marker in cleaned for marker in _HUMAN_MARKERS):
        return True
    # A bare affirmative counts only if the previous assistant message was
    # one of the fixed handoff offers. Otherwise "yes" to "lake view?" would
    # pull a staff member into a perfectly healthy conversation.
    if cleaned in _AFFIRMATIVES:
        return replies.is_handoff_offer(_last_assistant_message(history))
    return False


def _from_rules(
    text: str, hotel_name: str, history=None, *, degraded_from: str | None = None
) -> IntentResult:
    """Deterministic fallback: no model, no network, no key."""
    if _looks_like_escalation(text, history):
        return IntentResult(
            GuestIntent.escalation, method="rules", degraded_from=degraded_from
        )

    hit = smalltalk.match(text)
    if hit is None:
        # Not a recognised pleasantry. Treat as a hotel question so the
        # grounded path handles it, rather than guessing an answer.
        return IntentResult(
            GuestIntent.hotel_query, method="rules", degraded_from=degraded_from
        )
    return IntentResult(
        GuestIntent.smalltalk,
        reply=smalltalk.reply_for(hit, hotel_name=hotel_name),
        method="rules",
        degraded_from=degraded_from,
    )


def classify(
    text: str,
    *,
    hotel_name: str,
    language: Language,
    history: list[dict] | None = None,
) -> IntentResult:
    """Classify, and for small talk produce the reply in the same call.

    Synchronous: callers run it in a threadpool alongside the other model
    calls. Never raises - a failure degrades to the rules fallback.
    """
    if not model_router.fast_available():
        return _from_rules(text, hotel_name, history)

    system = SYSTEM.format(
        hotel_name=hotel_name,
        reply_language=_LANGUAGE_HINT.get(language, _LANGUAGE_HINT[Language.en]),
    )
    # A little history so follow-ups like "and you?" are read in context.
    prompt = text
    if history:
        recent = "\n".join(
            f"{m['role']}: {m['content']}" for m in history[-4:]
        )
        prompt = f"Conversation so far:\n{recent}\n\nLatest guest message:\n{text}"

    try:
        result = model_router._fast_call(system, prompt, max_tokens=800)
        intent, reply = _parse(result.text)
    except (model_router.ChatError, ValueError, KeyError, json.JSONDecodeError):
        # The fast tier WAS available and the call still failed, so this is
        # real degradation rather than a keyless configuration.
        return _from_rules(
            text, hotel_name, history, degraded_from=model_router.FAST_PROVIDER
        )

    # An unmistakable ask for a human overrides the model. Getting this
    # wrong strands the guest in the refusal loop this intent exists to
    # break, and the markers are specific enough not to fire by accident.
    if intent is not GuestIntent.escalation and _looks_like_escalation(text, history):
        return IntentResult(
            GuestIntent.escalation, method="rules-override", telemetry=result
        )

    return IntentResult(intent, reply=reply, method="model", telemetry=result)
