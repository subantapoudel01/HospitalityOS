"""
Language detection accuracy, measured rather than asserted.

English and Romanized Nepali share an alphabet, so some misclassification is
unavoidable. This file reports the actual rate so it can be tracked, and
fails only if the deterministic layers get *worse* than the recorded
baseline. No database and no model: pure functions only.
"""
from __future__ import annotations

import pytest

from app.modules.receptionist.services.language import Language, detect

# (text, expected). Expected None means "genuinely ambiguous - escalating to
# a model is the right answer, not a failure".
CASES: list[tuple[str, Language | None]] = [
    # --- English ---
    ("What time is check-out?", Language.en),
    ("How much is a deluxe room?", Language.en),
    ("Do you allow pets in the rooms?", Language.en),
    ("Is breakfast included?", Language.en),
    ("Where can I watch the sunrise?", Language.en),
    ("Can I pay by credit card?", Language.en),
    ("I would like to book a room for two nights", Language.en),
    ("Is there free wifi in the rooms?", Language.en),
    # --- Romanized Nepali ---
    ("kotha ko bhada kati ho?", Language.ne_romanized),
    ("check out kati baje ho?", Language.ne_romanized),
    ("malai wifi password chahiyo", Language.ne_romanized),
    ("tapai sanga khana cha?", Language.ne_romanized),
    ("kotha khali cha ki chaina?", Language.ne_romanized),
    ("hotel ma parking cha?", Language.ne_romanized),
    ("naasta kati baje suru huncha?", Language.ne_romanized),
    ("ma bholi aauchu", Language.ne_romanized),
    # --- Devanagari ---
    ("चेक-आउट कति बजे हो?", Language.ne_devanagari),
    ("के मसँग पाल्तु जनावर ल्याउन मिल्छ?", Language.ne_devanagari),
    ("कोठाको भाडा कति हो?", Language.ne_devanagari),
    ("नाश्ता कति बजे हुन्छ?", Language.ne_devanagari),
    # Mixed script still reads as Nepali from the guest's side.
    ("check-in कति बजे?", Language.ne_devanagari),
    # --- genuinely ambiguous, should escalate ---
    ("room?", None),
    ("wifi", None),
    ("ok", None),
]

# Recorded baseline. Raise these when detection improves; never lower them
# to make a regression pass.
MIN_DECIDED_ACCURACY = 1.0
MAX_UNCERTAIN = 4


def test_devanagari_is_always_certain():
    """Script detection is deterministic and must never escalate."""
    for text, expected in CASES:
        if expected is Language.ne_devanagari:
            d = detect(text)
            assert d.language is Language.ne_devanagari, text
            assert d.method == "script"
            assert d.confidence == 1.0


def test_detection_accuracy():
    decided = wrong = uncertain = 0
    failures: list[str] = []

    for text, expected in CASES:
        d = detect(text)
        if d.language is None:
            uncertain += 1
            if expected is not None:
                failures.append(f"  UNDECIDED (wanted {expected.value}): {text!r}")
            continue
        decided += 1
        if expected is not None and d.language is not expected:
            wrong += 1
            failures.append(
                f"  WRONG got {d.language.value} wanted {expected.value}: {text!r}"
            )

    accuracy = (decided - wrong) / decided if decided else 0.0
    print(f"\ndecided {decided}/{len(CASES)}, accuracy {accuracy:.0%}, "
          f"uncertain {uncertain}")
    if failures:
        print("\n".join(failures))

    assert accuracy >= MIN_DECIDED_ACCURACY, "\n".join(failures)
    assert uncertain <= MAX_UNCERTAIN, (
        f"{uncertain} messages escalated to a model; each one costs an API "
        f"call, so the heuristic should carry more of the load"
    )


@pytest.mark.parametrize("text", ["", "   ", "!!!", "123"])
def test_empty_and_junk_default_to_english(text):
    """Never crash, never escalate on punctuation or digits."""
    d = detect(text)
    assert d.language is Language.en


def test_uncertain_cases_are_flagged_not_guessed():
    for text in ("room?", "wifi", "ok"):
        assert detect(text).uncertain, (
            f"{text!r} is ambiguous and should escalate rather than be guessed"
        )
