"""
Booking slot validation — the part that must not depend on a model.

The LLM proposes dates; this logic decides whether they are storable. These
tests run with no database and no provider, because a wrong date in a
booking record is acted on by staff and is worse than no record at all.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.modules.receptionist.services.booking import (
    MAX_GUESTS,
    BookingSlots,
    confirmation_text,
    validate,
)
from app.modules.receptionist.services.language import Language

TODAY = date(2026, 8, 23)


def _slots(**kw) -> BookingSlots:
    base = dict(
        check_in_date=TODAY + timedelta(days=7),
        check_out_date=TODAY + timedelta(days=10),
        guest_count=2,
        room_type_preference=None,
    )
    base.update(kw)
    return BookingSlots(**base)


def test_valid_slots_survive_untouched():
    slots, problems = validate(_slots(), today=TODAY)
    assert problems == []
    assert slots.complete
    assert slots.missing == []


def test_past_check_in_is_rejected_not_corrected():
    """A date in the past is cleared so the guest is asked again."""
    slots, problems = validate(
        _slots(check_in_date=TODAY - timedelta(days=1)), today=TODAY
    )
    assert slots.check_in_date is None
    assert "check_in_date" in slots.missing
    assert any("past" in p for p in problems)


def test_check_in_today_is_allowed():
    slots, problems = validate(
        _slots(check_in_date=TODAY, check_out_date=TODAY + timedelta(days=1)),
        today=TODAY,
    )
    assert problems == []
    assert slots.check_in_date == TODAY


def test_check_out_before_check_in_is_rejected():
    slots, problems = validate(
        _slots(
            check_in_date=TODAY + timedelta(days=10),
            check_out_date=TODAY + timedelta(days=3),
        ),
        today=TODAY,
    )
    assert slots.check_out_date is None
    assert any("not after" in p for p in problems)


def test_same_day_check_out_is_rejected():
    """Zero nights is not a stay, and the DB check constraint forbids it."""
    day = TODAY + timedelta(days=5)
    slots, problems = validate(
        _slots(check_in_date=day, check_out_date=day), today=TODAY
    )
    assert slots.check_out_date is None
    assert problems


def test_absurd_stay_length_is_rejected():
    slots, problems = validate(
        _slots(check_out_date=TODAY + timedelta(days=800)), today=TODAY
    )
    assert slots.check_out_date is None
    assert any("longer than a year" in p for p in problems)


def test_check_in_far_in_the_future_is_rejected():
    slots, problems = validate(
        _slots(
            check_in_date=TODAY + timedelta(days=365 * 5),
            check_out_date=TODAY + timedelta(days=365 * 5 + 2),
        ),
        today=TODAY,
    )
    assert slots.check_in_date is None
    assert any("far ahead" in p for p in problems)


@pytest.mark.parametrize("count", [0, -3, MAX_GUESTS + 1, 999])
def test_implausible_guest_counts_are_rejected(count):
    slots, problems = validate(_slots(guest_count=count), today=TODAY)
    assert slots.guest_count is None
    assert "guest_count" in slots.missing
    assert problems


@pytest.mark.parametrize("count", [1, 2, MAX_GUESTS])
def test_plausible_guest_counts_survive(count):
    slots, problems = validate(_slots(guest_count=count), today=TODAY)
    assert slots.guest_count == count
    assert problems == []


def test_room_preference_is_trimmed_and_capped():
    slots, _ = validate(_slots(room_type_preference="  " + "x" * 400), today=TODAY)
    assert len(slots.room_type_preference) <= 120


def test_incomplete_slots_report_what_is_missing():
    slots = BookingSlots(check_in_date=TODAY + timedelta(days=2))
    assert not slots.complete
    assert set(slots.missing) == {"check_out_date", "guest_count"}


def test_confirmation_restates_the_stored_values_exactly():
    """The confirmation is templated so numbers cannot drift from the row."""
    slots = _slots(
        check_in_date=date(2026, 9, 12),
        check_out_date=date(2026, 9, 15),
        guest_count=2,
        room_type_preference="lake view",
    )
    text = confirmation_text(slots, language=Language.en)
    assert "12 Sep 2026" in text
    assert "15 Sep 2026" in text
    assert "3 night" in text
    assert "2 guest" in text
    assert "lake view" in text


def test_confirmation_singularises_one_night_one_guest():
    slots = _slots(
        check_in_date=date(2026, 9, 12),
        check_out_date=date(2026, 9, 13),
        guest_count=1,
    )
    text = confirmation_text(slots, language=Language.en)
    assert "1 night" in text and "1 nights" not in text
    assert "1 guest" in text and "1 guests" not in text


@pytest.mark.parametrize(
    "language", [Language.en, Language.ne_romanized, Language.ne_devanagari]
)
def test_confirmation_carries_the_numbers_in_every_language(language):
    slots = _slots(
        check_in_date=date(2026, 9, 12),
        check_out_date=date(2026, 9, 15),
        guest_count=4,
    )
    text = confirmation_text(slots, language=language)
    assert "2026" in text
    assert "4" in text
    assert "3" in text


# --- weekday consistency -------------------------------------------------
#
# Regression guard for a measured failure: on Sunday 23 Aug 2026 the model
# resolved "next Friday to next Sunday" to Wed 26 -> Fri 28. Both dates were
# future-dated and correctly ordered, so every other check passed.

from app.modules.receptionist.services.booking import weekday_check  # noqa: E402


def test_date_on_a_weekday_the_guest_never_mentioned_is_cleared():
    slots, problems = weekday_check(
        "I want next Friday to next Sunday",
        BookingSlots(
            check_in_date=date(2026, 8, 26),   # a Wednesday
            check_out_date=date(2026, 8, 28),  # a Friday
        ),
    )
    assert slots.check_in_date is None, "Wednesday was never mentioned"
    assert problems and "Wednesday" in problems[0]


def test_date_matching_a_mentioned_weekday_survives():
    slots, problems = weekday_check(
        "book me in for Friday",
        BookingSlots(check_in_date=date(2026, 8, 28)),  # a Friday
    )
    assert slots.check_in_date == date(2026, 8, 28)
    assert problems == []


def test_check_is_skipped_when_no_weekday_is_named():
    """Explicit calendar dates must never be second-guessed."""
    slots, problems = weekday_check(
        "check in 2026-09-12 for 3 nights",
        BookingSlots(
            check_in_date=date(2026, 9, 12), check_out_date=date(2026, 9, 15)
        ),
    )
    assert slots.check_in_date == date(2026, 9, 12)
    assert problems == []


@pytest.mark.parametrize(
    "text", ["sunrise at Sarangkot", "we were satisfied", "a moment please"]
)
def test_weekday_substrings_inside_other_words_do_not_trigger(text):
    """'sun' in 'sunrise' and 'sat' in 'satisfied' must not count."""
    slots, problems = weekday_check(
        text, BookingSlots(check_in_date=date(2026, 8, 26))
    )
    assert slots.check_in_date == date(2026, 8, 26)
    assert problems == []
