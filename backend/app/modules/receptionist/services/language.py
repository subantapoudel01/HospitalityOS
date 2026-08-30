"""
Language detection for guest messages (Slice D).

Three languages matter for the pilot, matching the values the design doc
already uses for faqs.language:

    en              English
    ne_romanized    Nepali written in the Latin alphabet ("kotha ko bhada
                    kati ho?") - overwhelmingly common in Nepali chat
    ne_devanagari   Nepali in Devanagari script

Detection is layered so that most messages cost nothing:

  1. Script. Devanagari occupies a dedicated Unicode block, so its presence
     is decisive. Free and certain.
  2. Heuristic. Latin script is ambiguous - English and Romanized Nepali
     share an alphabet - so the text is scored against Nepali function and
     content words. Strong signal in either direction resolves here.
  3. Model. Only genuinely ambiguous text (short, no markers, code-mixed)
     escalates to FAST_MODEL. The caller decides whether to pay for that.

The heuristic will be wrong sometimes; that is why `Detection` carries the
method and confidence, so a wrong answer can be traced to the layer that
produced it rather than being silently assumed correct.
"""
from __future__ import annotations

import enum
import re
from dataclasses import dataclass


class Language(str, enum.Enum):
    en = "en"
    ne_romanized = "ne_romanized"
    ne_devanagari = "ne_devanagari"

    @property
    def is_nepali(self) -> bool:
        return self is not Language.en


# Devanagari block. Includes the danda and Devanagari digits.
_DEVANAGARI = re.compile(r"[ऀ-ॿ]")
_WORD = re.compile(r"[a-z]+")

# Romanized Nepali markers. Function words carry the strongest signal
# because they appear regardless of topic and have no common English
# homographs. Content words are included because a guest question is often
# only three or four words long, too short for function words to show up.
_MARKERS: frozenset[str] = frozenset(
    {
        # question words and copulas
        "ke", "k", "kati", "kaha", "kahaa", "kahan", "kasari", "kina", "kun",
        "kasto", "kaslai", "cha", "chha", "xa", "chan", "chhan", "ho", "hoina",
        "hola", "huncha", "hunchha", "hunxa", "bhayo", "bhaye", "bhane",
        "garna", "garne", "garcha", "garchha", "dinu", "dincha", "paincha",
        "paunca", "paunchha", "sakincha", "sakinchha", "lagcha", "lagchha",
        # pronouns and particles
        "ma", "malai", "mero", "hami", "hamro", "timi", "timro", "tapai",
        "tapailai", "tapaiko", "yo", "tyo", "yahan", "tyahan", "ani", "ra",
        "ni", "po", "la", "nai", "matra", "aru", "kehi", "sabai", "pani",
        # hospitality vocabulary
        "kotha", "bhada", "bhaada", "khana", "khaja", "paani", "baje",
        "bihana", "saanjh", "sanjha", "raat", "din", "paisa", "manche",
        "sodhna", "sodhne", "chahiyo", "chaiyo", "milcha", "milchha",
        "namaste", "dhanyabad", "dhanyabaad", "swagatam", "hajur",
    }
)

# A marker ratio at or above this, with at least one marker, reads as Nepali.
_NEPALI_RATIO = 0.20
# Below this many words, absence of markers proves nothing either way.
_MIN_WORDS_FOR_ENGLISH = 3


@dataclass(frozen=True)
class Detection:
    """`language` is None when the deterministic layers could not decide."""

    language: Language | None
    method: str  # script | heuristic | model | default
    confidence: float
    marker_count: int = 0
    word_count: int = 0

    @property
    def uncertain(self) -> bool:
        return self.language is None


def markers_in(text: str) -> list[str]:
    words = _WORD.findall((text or "").lower())
    return [w for w in words if w in _MARKERS]


def detect(text: str) -> Detection:
    """Deterministic detection. Never calls a model."""
    text = (text or "").strip()
    if not text:
        return Detection(Language.en, "default", 0.0)

    if _DEVANAGARI.search(text):
        # Any Devanagari at all settles it. Mixed-script messages
        # ("check-in कति बजे?") are still Nepali from the guest's side.
        return Detection(Language.ne_devanagari, "script", 1.0)

    words = _WORD.findall(text.lower())
    if not words:
        return Detection(Language.en, "default", 0.0)

    hits = [w for w in words if w in _MARKERS]
    ratio = len(hits) / len(words)

    if hits and ratio >= _NEPALI_RATIO:
        return Detection(
            Language.ne_romanized,
            "heuristic",
            round(min(1.0, 0.5 + ratio), 3),
            marker_count=len(hits),
            word_count=len(words),
        )

    if not hits and len(words) >= _MIN_WORDS_FOR_ENGLISH:
        return Detection(
            Language.en, "heuristic", 0.7, marker_count=0, word_count=len(words)
        )

    # Short, or a weak scattering of markers. Could be either; let the
    # caller decide whether a model call is worth it.
    return Detection(
        None, "heuristic", 0.0, marker_count=len(hits), word_count=len(words)
    )


LANGUAGE_NAMES = {
    Language.en: "English",
    Language.ne_romanized: "Romanized Nepali (Nepali written in Latin script)",
    Language.ne_devanagari: "Nepali in Devanagari script",
}


def reply_instruction(language: Language) -> str:
    """How the generator should be told to write its answer."""
    if language is Language.ne_devanagari:
        return (
            "Reply in Nepali using Devanagari script. Keep hotel names, "
            "numbers and times as they appear in the context."
        )
    if language is Language.ne_romanized:
        return (
            "Reply in Romanized Nepali - Nepali written in the Latin "
            "alphabet, the way Nepali speakers text. Do not use Devanagari "
            "script. Keep hotel names, numbers and times as they appear in "
            "the context."
        )
    return "Reply in English."
