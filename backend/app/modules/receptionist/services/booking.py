"""
Booking request collection (Slice E, US-2).

The guest describes a trip in their own words ("2 people, next weekend") and
the system fills in check-in, check-out, guest count and room preference
across however many turns it takes - no form.

Two rules shape the design:

1. RE-EXTRACT FROM THE WHOLE CONVERSATION every turn, rather than keeping a
   partially-filled draft. Guests change their minds ("actually make it
   Friday"), and a running slot machine has to detect and unwind that. Re-
   reading the transcript gets corrections for free.

2. NEVER TRUST THE MODEL'S ARITHMETIC. It proposes dates; this module
   validates them. "Next weekend" resolved to a Tuesday, a check-out before
   check-in, or a date in the past is caught here, not stored. A wrong date
   in a booking record is worse than no record: staff act on it.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from app.core import model_router
from app.modules.receptionist.services.language import Language

_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

REQUIRED_SLOTS = ("check_in_date", "check_out_date", "guest_count")

# Sanity bounds. Not business rules - just limits beyond which the value is
# certainly an extraction error rather than a real request.
MAX_GUESTS = 30
MAX_NIGHTS = 365
MAX_MONTHS_AHEAD = 24


@dataclass
class BookingSlots:
    check_in_date: date | None = None
    check_out_date: date | None = None
    guest_count: int | None = None
    room_type_preference: str | None = None

    @property
    def missing(self) -> list[str]:
        return [s for s in REQUIRED_SLOTS if getattr(self, s) is None]

    @property
    def complete(self) -> bool:
        return not self.missing


@dataclass
class Extraction:
    slots: BookingSlots
    # Guest-facing descriptions of values that failed validation.
    problems: list[str] = field(default_factory=list)
    follow_up: str | None = None
    telemetry: model_router.ChatResult | None = None
    # Set when slot extraction fell back because a model call failed.
    degraded_from: str | None = None


SYSTEM = """You extract booking details from a hotel conversation.

Today is {today} ({weekday}). The hotel is in timezone {timezone}.

Read the whole conversation and return the guest's CURRENT intent. If they
changed their mind, use the latest value, not the first.

Return ONLY a JSON object, no code fences:
{{
  "check_in_date": "YYYY-MM-DD" or null,
  "check_out_date": "YYYY-MM-DD" or null,
  "guest_count": integer or null,
  "room_type_preference": string or null,
  "follow_up": string or null
}}

Rules:
- Resolve relative dates against today. "This weekend" means the coming
  Saturday to Sunday. "Next weekend" means the Saturday after that.
- If the guest gives a number of nights, compute check_out from check_in.
- Only fill a field if the guest actually indicated it. Never invent dates
  or guest counts to be helpful - null is the correct answer when unknown.
- room_type_preference is free text as the guest described it ("lake view",
  "family room"), or null.
- "follow_up": if anything required is still missing, write one short, warm
  question asking ONLY for what is missing. If everything is present, null.
- {reply_language}"""

_LANGUAGE_HINT = {
    Language.en: "Write follow_up in English.",
    Language.ne_romanized: (
        "Write follow_up in Romanized Nepali - Latin alphabet, no Devanagari."
    ),
    Language.ne_devanagari: "Write follow_up in Nepali using Devanagari script.",
}

_SLOT_LABELS = {
    "check_in_date": "check-in date",
    "check_out_date": "check-out date",
    "guest_count": "number of guests",
}


_WEEKDAYS = {
    "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
    "friday": 4, "saturday": 5, "sunday": 6,
    "mon": 0, "tue": 1, "tues": 1, "wed": 2, "thu": 3, "thur": 3,
    "thurs": 3, "fri": 4, "sat": 5, "sun": 6,
}
# Lookarounds instead of word boundaries: same effect for these
# all-letter tokens, and no backslash escape to survive. Without a
# boundary, "sun" matches inside "sunrise" and "sat" inside "satisfied",
# which would fire on transcripts that never named a weekday.
_WEEKDAY_RE = re.compile(
    "(?<![a-zA-Z])(" + "|".join(sorted(_WEEKDAYS, key=len, reverse=True)) + ")(?![a-zA-Z])",
    re.IGNORECASE,
)


def weekday_check(
    transcript: str, slots: BookingSlots
) -> tuple[BookingSlots, list[str]]:
    """Verify extracted dates land on the weekday the guest actually named.

    Measured failure this exists to catch: on Sunday 23 Aug 2026, "next
    Friday to next Sunday" was extracted as Wed 26 -> Fri 28. Both dates are
    in the future and correctly ordered, so every other check passed while
    the answer was simply wrong.

    Deliberately conservative - it only fires when the guest named weekdays
    at all, and only clears a date whose weekday matches none of them. A
    guest who gives explicit calendar dates is never second-guessed.
    """
    named = {
        _WEEKDAYS[m.group(1).lower()] for m in _WEEKDAY_RE.finditer(transcript or "")
    }
    if not named:
        return slots, []

    problems: list[str] = []
    for field_name, label in (
        ("check_in_date", "check-in"),
        ("check_out_date", "check-out"),
    ):
        value = getattr(slots, field_name)
        if value is not None and value.weekday() not in named:
            problems.append(
                f"the {label} date I worked out ({value:%A %d %b}) does not "
                "fall on the day you mentioned"
            )
            setattr(slots, field_name, None)
    return slots, problems


def _parse_date(value) -> date | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip()[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def validate(slots: BookingSlots, *, today: date) -> tuple[BookingSlots, list[str]]:
    """Drop values that cannot be right, and say why in guest-facing terms.

    Rejected values are cleared rather than corrected, so the guest is asked
    again instead of being silently booked into a guessed date.
    """
    problems: list[str] = []

    if slots.check_in_date and slots.check_in_date < today:
        problems.append(
            f"the check-in date I understood ({slots.check_in_date:%d %b %Y}) "
            "is in the past"
        )
        slots.check_in_date = None

    if slots.check_in_date and slots.check_in_date > today + timedelta(
        days=31 * MAX_MONTHS_AHEAD
    ):
        problems.append(
            f"the check-in date I understood ({slots.check_in_date:%d %b %Y}) "
            "is unusually far ahead"
        )
        slots.check_in_date = None

    if slots.check_in_date and slots.check_out_date:
        if slots.check_out_date <= slots.check_in_date:
            problems.append(
                "the check-out date is not after the check-in date"
            )
            slots.check_out_date = None
        elif (slots.check_out_date - slots.check_in_date).days > MAX_NIGHTS:
            problems.append("that stay is longer than a year")
            slots.check_out_date = None
    elif slots.check_out_date and not slots.check_in_date:
        # A check-out with no check-in cannot be range-checked; keep it, the
        # guest will be asked for check-in next.
        pass

    if slots.guest_count is not None:
        if slots.guest_count < 1 or slots.guest_count > MAX_GUESTS:
            problems.append(
                f"a party of {slots.guest_count} does not look right"
            )
            slots.guest_count = None

    if slots.room_type_preference:
        slots.room_type_preference = slots.room_type_preference.strip()[:120] or None

    return slots, problems


def _fallback_follow_up(missing: list[str]) -> str:
    labels = [_SLOT_LABELS[m] for m in missing]
    if len(labels) == 1:
        joined = labels[0]
    elif len(labels) == 2:
        joined = f"{labels[0]} and {labels[1]}"
    else:
        joined = ", ".join(labels[:-1]) + f", and {labels[-1]}"
    return f"Happy to help with that. Could you tell me the {joined}?"


def extract(
    transcript: str,
    *,
    today: date,
    timezone: str,
    language: Language,
) -> Extraction:
    """Pull booking slots out of the conversation, then validate them."""
    if not model_router.fast_available():
        # No model: cannot parse natural-language dates. Ask plainly rather
        # than guessing.
        slots = BookingSlots()
        return Extraction(
            slots=slots,
            follow_up=_fallback_follow_up(list(REQUIRED_SLOTS)),
        )

    system = SYSTEM.format(
        today=today.isoformat(),
        weekday=today.strftime("%A"),
        timezone=timezone or "Asia/Kathmandu",
        reply_language=_LANGUAGE_HINT.get(language, _LANGUAGE_HINT[Language.en]),
    )

    try:
        result = model_router._fast_call(system, transcript, max_tokens=900)
        match = _JSON_BLOCK.search(result.text or "")
        if not match:
            raise ValueError("no JSON in extraction response")
        data = json.loads(match.group(0))
    except (model_router.ChatError, ValueError, json.JSONDecodeError):
        # Available but failed: the guest gets a generic question instead of
        # having their dates understood, and that is worth recording.
        slots = BookingSlots()
        return Extraction(
            slots=slots,
            follow_up=_fallback_follow_up(list(REQUIRED_SLOTS)),
            degraded_from=model_router.FAST_PROVIDER,
        )

    guest_count = data.get("guest_count")
    try:
        guest_count = int(guest_count) if guest_count is not None else None
    except (TypeError, ValueError):
        guest_count = None

    room = data.get("room_type_preference")
    slots = BookingSlots(
        check_in_date=_parse_date(data.get("check_in_date")),
        check_out_date=_parse_date(data.get("check_out_date")),
        guest_count=guest_count,
        room_type_preference=str(room) if room else None,
    )
    slots, problems = validate(slots, today=today)
    slots, weekday_problems = weekday_check(transcript, slots)
    problems.extend(weekday_problems)

    follow_up = data.get("follow_up")
    follow_up = str(follow_up).strip() if follow_up else None
    if slots.missing and not follow_up:
        follow_up = _fallback_follow_up(slots.missing)
    if problems:
        # The model's own follow-up will not mention a value we just
        # rejected, so replace it with one that does.
        detail = "; ".join(problems)
        follow_up = (
            f"Sorry, {detail}. " + _fallback_follow_up(slots.missing)
            if slots.missing
            else f"Sorry, {detail}. Could you confirm the dates?"
        )

    return Extraction(
        slots=slots, problems=problems, follow_up=follow_up, telemetry=result
    )


def confirmation_text(slots: BookingSlots, *, language: Language) -> str:
    """Read the saved values back. Templated on purpose.

    A confirmation restates facts that are about to be acted on by staff, so
    it must match the stored row exactly. Letting a model paraphrase it
    invites a number to drift between what the guest saw and what was saved.
    """
    nights = (slots.check_out_date - slots.check_in_date).days
    check_in = f"{slots.check_in_date:%a %d %b %Y}"
    check_out = f"{slots.check_out_date:%a %d %b %Y}"
    guests = slots.guest_count
    room = slots.room_type_preference

    if language is Language.ne_devanagari:
        base = (
            f"तपाईंको अनुरोध नोट गरेँ: {check_in} देखि {check_out} सम्म "
            f"({nights} रात), {guests} जना।"
        )
        if room:
            base += f" कोठाको रुचि: {room}।"
        return base + " हाम्रो स्टाफले चाँडै सम्पर्क गर्नुहुनेछ।"

    if language is Language.ne_romanized:
        base = (
            f"Tapai ko request note garey: {check_in} dekhi {check_out} samma "
            f"({nights} raat), {guests} jana."
        )
        if room:
            base += f" Room preference: {room}."
        return base + " Hamro staff le chandai sampark garnu hunecha."

    base = (
        f"I have noted your request: {check_in} to {check_out} "
        f"({nights} night{'s' if nights != 1 else ''}) for {guests} "
        f"guest{'s' if guests != 1 else ''}."
    )
    if room:
        base += f" Room preference: {room}."
    return base + " Our staff will contact you shortly to confirm."
