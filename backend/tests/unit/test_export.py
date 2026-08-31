"""
CSV export of booking inquiries (US-8).

These are the properties that decide whether the file is usable by the
person who opens it, rather than merely well-formed.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime, timezone

from app.modules.receptionist.services import export


@dataclass
class FakeInquiry:
    """Shaped like BookingInquiry, with no database behind it. The export
    only reads attributes, so exercising it with real rows would test
    SQLAlchemy rather than the formatting."""

    id: int = 1
    status: str = "new"
    check_in_date: date | None = date(2026, 9, 12)
    check_out_date: date | None = date(2026, 9, 15)
    guest_count: int | None = 2
    room_type_preference: str | None = "Lake view"
    created_at: datetime = datetime(2026, 8, 31, 9, 30, tzinfo=timezone.utc)
    conversation_id: int = 42
    raw_request: str | None = "We'd like a room for 2 from the 12th to the 15th"


def parse(text: str) -> list[dict]:
    return list(csv.DictReader(io.StringIO(text)))


def test_headers_are_stable():
    """Staff build spreadsheet formulas against these. Reordering or
    renaming a column silently breaks work that lives outside this repo."""
    rows = list(csv.reader(io.StringIO(export.to_csv([]))))
    assert rows[0] == export.HEADERS


def test_an_empty_export_is_still_a_valid_file():
    """Not an empty download. A header-only CSV opens and shows the
    columns, which reads as 'nothing yet' rather than as a failure."""
    text = export.to_csv([])
    assert text.strip()
    assert parse(text) == []


def test_a_row_carries_the_facts_staff_act_on():
    row = parse(export.to_csv([FakeInquiry()]))[0]
    assert row["inquiry_id"] == "1"
    assert row["check_in"] == "2026-09-12"
    assert row["check_out"] == "2026-09-15"
    assert row["guests"] == "2"
    assert row["room_preference"] == "Lake view"
    assert row["conversation_id"] == "42"


def test_nights_are_computed_not_left_to_the_spreadsheet():
    assert parse(export.to_csv([FakeInquiry()]))[0]["nights"] == "3"


def test_the_guests_own_words_are_included():
    """The dates are a model's reading of the message. Without the
    original text a misparse is invisible to the person acting on it."""
    row = parse(export.to_csv([FakeInquiry()]))[0]
    assert "12th to the 15th" in row["guest_own_words"]


def test_newlines_in_the_guest_message_do_not_break_the_row():
    """Legal inside a quoted field, but it turns one inquiry into six
    visual rows in Excel."""
    text = export.to_csv([FakeInquiry(raw_request="line one\nline two\r\nline three")])
    rows = parse(text)
    assert len(rows) == 1
    assert rows[0]["guest_own_words"] == "line one line two line three"


def test_commas_and_quotes_survive_a_round_trip():
    nasty = 'We want a "lake view" room, 2 adults, 1 child'
    row = parse(export.to_csv([FakeInquiry(raw_request=nasty)]))[0]
    assert row["guest_own_words"] == nasty


def test_a_formula_looking_message_is_not_executable():
    """CSV injection. This column is whatever the guest typed into the
    chat widget, and it opens in a staff member's spreadsheet - a leading
    = is evaluated on open."""
    row = parse(export.to_csv([FakeInquiry(raw_request="=1+1")]))[0]
    assert row["guest_own_words"] == "'=1+1", "must be quoted as text"
    # Still one row, and the guest's words are still legible.
    assert row["inquiry_id"] == "1"
    assert "1+1" in row["guest_own_words"]


def test_every_formula_lead_character_is_defused():
    for lead in ("=", "+", "-", "@"):
        payload = lead + "cmd|'/c calc'!A1"
        row = parse(export.to_csv([FakeInquiry(raw_request=payload)]))[0]
        assert row["guest_own_words"].startswith("'" + lead), lead


def test_ordinary_text_is_left_alone():
    """The mitigation must not apostrophise every message."""
    row = parse(export.to_csv([FakeInquiry(raw_request="2 adults, lake view")]))[0]
    assert row["guest_own_words"] == "2 adults, lake view"


def test_devanagari_survives():
    row = parse(export.to_csv([FakeInquiry(raw_request="नमस्ते, कोठा चाहियो")]))[0]
    assert "नमस्ते" in row["guest_own_words"]


def test_missing_optional_values_become_empty_not_none():
    """'None' in a spreadsheet cell reads as data. Blank reads as blank."""
    row = parse(
        export.to_csv(
            [FakeInquiry(room_type_preference=None, raw_request=None,
                         guest_count=None)]
        )
    )[0]
    assert row["room_preference"] == ""
    assert row["guest_own_words"] == ""
    assert row["guests"] == ""
    assert "None" not in ",".join(row.values())


def test_missing_dates_do_not_crash_the_nights_calculation():
    row = parse(export.to_csv([FakeInquiry(check_out_date=None)]))[0]
    assert row["nights"] == ""
    assert row["check_out"] == ""


def test_filename_names_the_hotel_and_the_day():
    name = export.filename(7, today=date(2026, 8, 31))
    assert name == "booking-inquiries-hotel7-2026-08-31.csv"
