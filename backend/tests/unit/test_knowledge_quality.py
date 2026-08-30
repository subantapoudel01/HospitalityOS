"""
Knowledge quality checks.

Exists because of a measured hallucination: a hotel whose entire knowledge
base was a policy reading "10/12" answered "What time is check-out?" with
"Check-out is at 12:00 pm", three runs out of three, in two languages. The
retrieval score was 0.54 - well above the floor - and the grounding prompt
did not object, because the chunk genuinely is about check-out.

No prompt fixes source data that carries no meaning. This catches it at the
point a human types it.
"""
from __future__ import annotations

import pytest

from app.modules.receptionist.rag.quality import assess, worst_severity


def codes(text: str) -> set[str]:
    return {w.code for w in assess(text)}


# --- the case that motivated this ---------------------------------------


def test_the_policy_that_caused_the_hallucination_is_flagged():
    warnings = assess("10/12")
    assert warnings, "'10/12' must not pass as an answerable policy"
    assert worst_severity(warnings) == "high"
    assert "numbers_only" in {w.code for w in warnings}


@pytest.mark.parametrize("text", ["10/12", "2-4", "1400", "12:00", "1/1/2026"])
def test_bare_numbers_are_flagged(text):
    assert worst_severity(assess(text)) == "high"


# --- must not flag legitimate short entries -----------------------------


@pytest.mark.parametrize(
    "text",
    [
        "No pets.",
        "Check-in is from 2 PM and check-out is by 11 AM.",
        "Free cancellation up to 48 hours before arrival.",
        "Deluxe Lake View rooms cost NPR 4500 per night and sleep two guests.",
        "We accept Visa, Mastercard, eSewa and cash.",
    ],
)
def test_real_policies_are_not_flagged_as_unanswerable(text):
    """A false positive here nags staff into ignoring the warning."""
    assert "not_readable" not in codes(text)
    assert "numbers_only" not in codes(text)


def test_short_but_readable_gets_advice_not_alarm():
    warnings = assess("No pets.")
    assert worst_severity(warnings) == "low"
    assert all(w.severity == "low" for w in warnings)


def test_prose_of_reasonable_length_is_clean():
    assert assess("Check-in is from 2 PM and check-out is by 11 AM.") == []


# --- edges ---------------------------------------------------------------


def test_empty_is_flagged():
    assert {w.code for w in assess("")} == {"empty"}
    assert {w.code for w in assess("   ")} == {"empty"}


def test_single_word_is_not_answerable():
    assert "not_readable" in codes("wifi")


def test_devanagari_prose_is_accepted():
    """The check must not mistake a non-Latin script for gibberish."""
    text = "चेक-इन दिउँसो २ बजेबाट सुरु हुन्छ र चेक-आउट बिहान ११ बजेसम्म हो।"
    assert "not_readable" not in codes(text)
    assert "numbers_only" not in codes(text)


def test_romanized_nepali_prose_is_accepted():
    assert "not_readable" not in codes(
        "Check-in bihana 2 baje bata suru huncha."
    )


def test_label_appears_in_the_message():
    warnings = assess("10/12", label="The Checkin Checkout policy")
    assert any("Checkin Checkout policy" in w.message for w in warnings)


def test_worst_severity_ordering():
    assert worst_severity([]) is None
    assert worst_severity(assess("No pets.")) == "low"
    assert worst_severity(assess("10/12")) == "high"
