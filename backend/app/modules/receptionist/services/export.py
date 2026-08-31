"""
CSV export of booking inquiries (US-8).

Staff work bookings in a spreadsheet, not in this dashboard. The point of
the export is to get the inquiry out of here and into the tool they
already use, so the column set is what a person needs to act on a booking,
not a dump of the table.

Two details that are not incidental:

  * The file opens in Excel with the right encoding. UTF-8 without a BOM
    renders `नमस्ते` as mojibake in Excel on Windows, which is exactly
    where these files are going.

  * `raw_request` is included. The parsed dates are a model's reading of
    what the guest said; staff need the guest's own words to sanity-check
    it before they reply. Dropping the column would hide the one thing
    that makes a wrong date noticeable.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime

from app.modules.receptionist.models import BookingInquiry

HEADERS = [
    "inquiry_id",
    "status",
    "check_in",
    "check_out",
    "nights",
    "guests",
    "room_preference",
    "received_at",
    "conversation_id",
    "guest_own_words",
]


def _nights(check_in: date | None, check_out: date | None) -> str:
    if not check_in or not check_out:
        return ""
    return str(max((check_out - check_in).days, 0))


#: Leading characters that make Excel and Google Sheets treat a cell as a
#: formula rather than as text.
_FORMULA_LEADS = ("=", "+", "-", "@")


def _defuse(text: str) -> str:
    """Stop a guest's message being executed as a spreadsheet formula.

    This column carries whatever the guest typed into the chat widget, and
    it lands in a spreadsheet a staff member opens. A message beginning
    `=` is evaluated on open - the CSV injection class, which at its worst
    reaches DDE and at its mildest silently rewrites the cell so staff
    never see what the guest actually said.

    The fix is a leading apostrophe, which both Excel and Sheets read as
    "this is text". It is visible in the cell, and that is the accepted
    cost: a slightly ugly cell beats an executed one, and the guest's
    words are still there in full.
    """
    if text and text[0] in _FORMULA_LEADS:
        return "'" + text
    return text


def _clean(value: object) -> str:
    """Flatten for a spreadsheet cell.

    Newlines inside a quoted CSV field are legal and Excel reads them, but
    they make the sheet unreadable - one inquiry spanning six visual rows.
    """
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, date):
        return value.isoformat()
    return _defuse(" ".join(str(value).split()))


def to_csv(inquiries: list[BookingInquiry]) -> str:
    buffer = io.StringIO()
    # QUOTE_MINIMAL with the default dialect: Excel, LibreOffice and
    # pandas all read this without options.
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(HEADERS)

    for q in inquiries:
        writer.writerow(
            [
                q.id,
                q.status.value if hasattr(q.status, "value") else q.status,
                _clean(q.check_in_date),
                _clean(q.check_out_date),
                _nights(q.check_in_date, q.check_out_date),
                q.guest_count if q.guest_count is not None else "",
                _clean(q.room_type_preference),
                _clean(q.created_at),
                q.conversation_id,
                _clean(q.raw_request),
            ]
        )

    return buffer.getvalue()


def filename(hotel_id: int, *, today: date | None = None) -> str:
    stamp = (today or date.today()).isoformat()
    return f"booking-inquiries-hotel{hotel_id}-{stamp}.csv"
