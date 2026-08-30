"""
Conversational pleasantries, handled without touching the knowledge base.

A guest opening with "hello" was previously run through retrieval like any
other question, scored 0.014, fell below the similarity floor and got the
strict "I do not have that information" refusal. That is correct behaviour
for an unanswerable *question* and completely wrong for a greeting.

Matching is deliberately deterministic and whole-message only:

  * Deterministic - no model call, no API key, no quota, no latency. A
    greeting is a tiny closed set; spending an inference call to recognise
    "hi" would be absurd.
  * Whole-message - "hello, what time is check-out?" is a real question with
    a greeting stuck on the front, and must go down the retrieval path. Only
    a message that is *nothing but* pleasantry short-circuits.

Nepali forms are included from the start so Slice D gets this for free.
"""
from __future__ import annotations

import enum
import re
import unicodedata
from dataclasses import dataclass


class SmallTalkIntent(str, enum.Enum):
    greeting = "greeting"
    thanks = "thanks"
    farewell = "farewell"
    capability = "capability"


# Longest plausible pleasantry, in words. Anything wordier is a real message
# even if it happens to start with a greeting.
MAX_WORDS = 6

_PUNCT = re.compile(r"[^\w\sऀ-ॿ]+", re.UNICODE)
_SPACES = re.compile(r"\s+")


def normalise(text: str) -> str:
    """Lowercase, strip punctuation and emoji, collapse whitespace."""
    text = unicodedata.normalize("NFKC", text or "").strip().lower()
    # Drop emoji and symbols but keep Devanagari.
    text = "".join(
        ch for ch in text if not unicodedata.category(ch).startswith("So")
    )
    text = _PUNCT.sub(" ", text)
    return _SPACES.sub(" ", text).strip()


# (phrases, intent, language). Language is the language the phrase is *in*,
# which lets a Nepali greeting be answered in Nepali before any detection or
# translation machinery gets involved.
_PHRASES: list[tuple[set[str], SmallTalkIntent, str]] = [
    (
        {
            "hi", "hii", "hiii", "hello", "helo", "hey", "heya", "hiya", "yo",
            "hi there", "hello there", "hey there", "good morning",
            "good afternoon", "good evening", "good day", "greetings",
            "howdy", "hello hello", "hi hi", "morning", "evening",
        },
        SmallTalkIntent.greeting,
        "en",
    ),
    (
        {
            "thanks", "thank you", "thankyou", "thanks a lot", "thanks so much",
            "thank you so much", "thank you very much", "thx", "ty",
            "much appreciated", "appreciate it", "cheers", "perfect thanks",
            "great thanks", "ok thanks", "okay thanks", "thanks again",
        },
        SmallTalkIntent.thanks,
        "en",
    ),
    (
        {
            "bye", "goodbye", "bye bye", "good bye", "see you", "see ya",
            "see you later", "good night", "goodnight", "take care", "later",
            "that is all", "thats all", "no thanks", "nothing else",
        },
        SmallTalkIntent.farewell,
        "en",
    ),
    (
        {
            "what can you do", "what do you do", "who are you", "what are you",
            "are you a bot", "are you a robot", "are you human", "are you real",
            "are you an ai", "how does this work", "what is this", "help",
            "can you help", "can you help me", "what can i ask",
        },
        SmallTalkIntent.capability,
        "en",
    ),
    (
        {
            "namaste", "namaskar", "namaskaar", "k cha", "ke cha", "kd cha",
            "kasto cha", "kasto chha", "k xa", "ke xa", "namaste ji",
        },
        SmallTalkIntent.greeting,
        "ne_romanized",
    ),
    (
        {"dhanyabad", "dhanyabaad", "dhanyawad", "dhanyavad", "thank you hai"},
        SmallTalkIntent.thanks,
        "ne_romanized",
    ),
    (
        {"bidai", "pheri betaula", "pheri vetaula", "subha ratri", "shubha ratri"},
        SmallTalkIntent.farewell,
        "ne_romanized",
    ),
    (
        {
            "नमस्ते",          # namaste
            "नमस्कार",    # namaskar
            "के छ",                            # ke cha
            "कस्तो छ",          # kasto cha
        },
        SmallTalkIntent.greeting,
        "ne_devanagari",
    ),
    (
        {"धन्यवाद"},       # dhanyavaad
        SmallTalkIntent.thanks,
        "ne_devanagari",
    ),
    (
        {
            "शुभ रात्रि",  # subha ratri
            "फेरि भेटौंला",  # pheri bhetaunla
        },
        SmallTalkIntent.farewell,
        "ne_devanagari",
    ),
]

_LOOKUP: dict[str, tuple[SmallTalkIntent, str]] = {}
for _phrases, _intent, _lang in _PHRASES:
    for _p in _phrases:
        _LOOKUP[_p] = (_intent, _lang)


@dataclass(frozen=True)
class SmallTalkMatch:
    intent: SmallTalkIntent
    language: str


def match(text: str) -> SmallTalkMatch | None:
    """Return a match only if the whole message is pleasantry."""
    cleaned = normalise(text)
    if not cleaned:
        return None
    if len(cleaned.split()) > MAX_WORDS:
        return None
    hit = _LOOKUP.get(cleaned)
    if hit is None:
        return None
    intent, language = hit
    return SmallTalkMatch(intent=intent, language=language)


_REPLIES: dict[str, dict[SmallTalkIntent, str]] = {
    "en": {
        SmallTalkIntent.greeting: (
            "Hello, and welcome to {hotel}. I can help with rooms and rates, "
            "check-in and check-out, dining, policies, and things to do "
            "nearby. What would you like to know?"
        ),
        SmallTalkIntent.thanks: (
            "You are very welcome. Is there anything else I can help you with?"
        ),
        SmallTalkIntent.farewell: (
            "Thank you for chatting with {hotel}. Do come back if anything "
            "else comes up."
        ),
        SmallTalkIntent.capability: (
            "I am an AI assistant for {hotel}. I answer from the hotel's own "
            "information, so I can help with rooms and rates, check-in and "
            "check-out times, dining, policies, and nearby attractions. If I "
            "do not know something, I will say so and pass you to a staff "
            "member."
        ),
    },
    "ne_romanized": {
        SmallTalkIntent.greeting: (
            "Namaste, and welcome to {hotel}. I can help with rooms and rates, "
            "check-in and check-out, dining, and nearby attractions. Tapai "
            "lai ke chahiyo?"
        ),
        SmallTalkIntent.thanks: (
            "Swagatam. Aru kehi sodhnu cha ki?"
        ),
        SmallTalkIntent.farewell: (
            "Dhanyabad for chatting with {hotel}. Pheri bhetaula."
        ),
        SmallTalkIntent.capability: (
            "Ma {hotel} ko AI assistant hu. I answer from the hotel's own "
            "information: rooms, rates, check-in, dining, and nearby places. "
            "Thaha nabhaye, ma staff lai bolauchu."
        ),
    },
    "ne_devanagari": {
        SmallTalkIntent.greeting: (
            "नमस्ते! {hotel} मा स्वागत छ। म कोठा र भाडा, चेक-इन र चेक-आउट, "
            "खाना, र नजिकका घुम्ने ठाउँबारे जानकारी दिन सक्छु। "
            "तपाईंलाई के थाहा पाउनु छ?"
        ),
        SmallTalkIntent.thanks: (
            "स्वागतम्। अरू केही सोध्नु छ?"
        ),
        SmallTalkIntent.farewell: (
            "धन्यवाद। फेरि भेटौंला।"
        ),
        SmallTalkIntent.capability: (
            "म {hotel} को AI सहायक हुँ। म होटेलको आफ्नै जानकारीबाट मात्र "
            "जवाफ दिन्छु: कोठा, भाडा, चेक-इन, खाना, र नजिकका ठाउँ। "
            "मलाई थाहा भएन भने म स्टाफलाई बोलाउँछु।"
        ),
    },
}


def reply_for(match_: SmallTalkMatch, *, hotel_name: str) -> str:
    table = _REPLIES.get(match_.language, _REPLIES["en"])
    template = table.get(match_.intent, _REPLIES["en"][match_.intent])
    return template.format(hotel=hotel_name)
