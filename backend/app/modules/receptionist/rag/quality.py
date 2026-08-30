"""
Is this knowledge entry actually answerable from?

Motivated by a measured hallucination. A hotel's entire knowledge base was
one policy reading "10/12". Asked "what time is check-out?", the system
answered "Check-out is at 12:00 pm" - three times out of three, in two
languages. The retrieval score was 0.54, far above the floor, and the
grounding prompt did not object because the chunk genuinely IS about
check-out. It was grounded and still wrong.

No prompt can rescue source data that carries no meaning. The durable fix is
to notice at entry time that "10/12" cannot answer a question, and tell the
person typing it while they are still looking at the field.

These are WARNINGS, never blocks. Staff know their property better than a
heuristic does, and refusing to save their text would be worse than letting
them save something imperfect.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# A "word" for our purposes carries meaning: at least two letters. "10/12"
# has none, "No pets." has two.
_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

# Below this, an entry is unlikely to answer anything on its own.
MIN_MEANING_WORDS = 2
SHORT_ENTRY_CHARS = 25


@dataclass(frozen=True)
class Warning_:
    code: str
    severity: str  # high | low
    message: str


def meaning_words(text: str) -> list[str]:
    return _WORD.findall(text or "")


def assess(text: str, *, label: str = "This entry") -> list[Warning_]:
    """Flag content a guest question cannot be answered from."""
    content = (text or "").strip()
    warnings: list[Warning_] = []

    if not content:
        return [
            Warning_(
                "empty", "high", f"{label} is empty."
            )
        ]

    words = meaning_words(content)

    if len(words) < MIN_MEANING_WORDS:
        warnings.append(
            Warning_(
                "not_readable",
                "high",
                f"{label} does not read as a sentence, so the assistant "
                f"cannot answer questions from it - and may guess at what it "
                f"means. Write it the way you would say it to a guest, for "
                f'example "Check-in is from 2 PM and check-out is by 11 AM."',
            )
        )
    elif len(content) < SHORT_ENTRY_CHARS:
        warnings.append(
            Warning_(
                "very_short",
                "low",
                f"{label} is very short. Adding a little more detail helps "
                f"the assistant answer follow-up questions.",
            )
        )

    # Digits with no unit or context: "10/12", "2-4", "1400".
    stripped = re.sub(r"[\s\d/\-.:,]+", "", content)
    if content and not stripped:
        warnings.append(
            Warning_(
                "numbers_only",
                "high",
                f"{label} is only numbers, so its meaning is ambiguous - "
                f'"10/12" could be hours, dates, or a month and day. Spell '
                f"it out in words.",
            )
        )

    return warnings


def worst_severity(warnings: list[Warning_]) -> str | None:
    if any(w.severity == "high" for w in warnings):
        return "high"
    if warnings:
        return "low"
    return None
