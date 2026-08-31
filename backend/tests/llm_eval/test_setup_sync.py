"""
Setup data reaching the knowledge base.

The failure this covers is quiet and complete: room types entered at
/setup were stored, shown back in the form, and never synced. The rate
card was visible to the staff member who typed it and invisible to every
guest who asked "how much is a room" - the assistant refused to answer
from data the hotel had already given it.
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.core import model_router
from app.modules.receptionist.models import KnowledgeDocument
from app.modules.receptionist.rag import ingest, retrieval
from app.platform.models import HotelPolicy, PolicyCategory, RoomType

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _rules_only(monkeypatch):
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")


async def _setup(db, hotel, *, rooms=True, policies=True):
    if rooms:
        db.add_all([
            RoomType(
                hotel_id=hotel.id, name="Deluxe Room",
                base_rate=Decimal("10500.00"), max_occupancy=2,
                amenities=["Mountain View Balcony", "AC", "High-Speed Wi-Fi"],
            ),
            RoomType(
                hotel_id=hotel.id, name="Family Cottage",
                base_rate=Decimal("22000.00"), max_occupancy=4,
                amenities=["Private Garden", "Two Bedrooms"],
            ),
        ])
    if policies:
        db.add(
            HotelPolicy(
                hotel_id=hotel.id, category=PolicyCategory.cancellation,
                content_text=(
                    "Free cancellation up to 48 hours before check-in."
                ),
            )
        )
    await db.flush()


async def _titles(db, hotel_id) -> list[str]:
    rows = await db.execute(
        select(KnowledgeDocument.title).where(
            KnowledgeDocument.hotel_id == hotel_id
        )
    )
    return list(rows.scalars().all())


# --- room types reach the knowledge base --------------------------------


async def test_room_types_are_synced(db, hotel):
    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    titles = await _titles(db, hotel.id)
    assert any("Deluxe Room" in t for t in titles)
    assert any("Family Cottage" in t for t in titles)


async def test_policies_are_still_synced(db, hotel):
    """The room-type addition must not have displaced them."""
    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)
    assert any("Cancellation" in t for t in await _titles(db, hotel.id))


async def test_a_rate_question_retrieves_the_room(db, hotel):
    """The point of the whole exercise: a guest asking about price gets
    the rate card back, not a refusal."""
    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="How much is a deluxe room per night?",
        limit=3,
    )
    assert hits, "a rate question must retrieve something"
    assert "10,500" in hits[0].chunk_text
    assert hits[0].score > 0.3


async def test_an_occupancy_question_retrieves_the_room(db, hotel):
    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="Which room sleeps four people?", limit=3
    )
    joined = " ".join(h.chunk_text for h in hits)
    assert "Family Cottage" in joined
    assert "4 guests" in joined


# --- how the text is written --------------------------------------------


async def test_the_rate_is_written_the_way_a_guest_reads_it(db, hotel):
    """Thousands separator, no trailing .00 on a whole-rupee rate.

    An early version stripped trailing zeros from the formatted string,
    which turned "10,500" into "10,5".
    """
    room = RoomType(
        hotel_id=hotel.id, name="Deluxe Room", base_rate=Decimal("10500.00"),
        max_occupancy=2, amenities=[],
    )
    body = ingest._room_type_document(room, "NPR")
    assert "NPR 10,500 per night" in body
    assert "10,5 " not in body
    assert ".00" not in body


async def test_paise_are_kept_when_the_rate_actually_has_them():
    from app.platform.models import RoomType as RT

    room = RT(name="Odd Rate", base_rate=Decimal("1234.50"),
              max_occupancy=2, amenities=[])
    assert "1,234.50" in ingest._room_type_document(room, "NPR")


async def test_nothing_is_invented_for_a_bare_room_type():
    """No amenities entered means no amenities sentence - not an empty
    list rendered as fact, and certainly not a plausible guess."""
    from app.platform.models import RoomType as RT

    room = RT(name="Simple Room", base_rate=Decimal("2000"),
              max_occupancy=1, amenities=[], description=None)
    body = ingest._room_type_document(room, "NPR")
    assert "includes" not in body.lower()
    assert "1 guest" in body, "singular, not '1 guests'"


# --- re-running -----------------------------------------------------------


async def test_resyncing_replaces_rather_than_duplicates(db, hotel):
    """Staff will press this after every edit. Duplicates would give
    retrieval several copies of the same rate to disagree about."""
    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)
    first = await _titles(db, hotel.id)

    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)
    second = await _titles(db, hotel.id)

    assert sorted(first) == sorted(second)
    assert len(second) == len(set(second))


async def test_an_edited_rate_reaches_guests_after_a_resync(db, hotel):
    """/setup stays the single source of truth: change the rate there,
    re-sync, and the assistant quotes the new one."""
    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    room = (
        await db.execute(
            select(RoomType).where(
                RoomType.hotel_id == hotel.id, RoomType.name == "Deluxe Room"
            )
        )
    ).scalar_one()
    room.base_rate = Decimal("12000.00")
    await db.flush()
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="deluxe room price per night", limit=3
    )
    joined = " ".join(h.chunk_text for h in hits)
    assert "12,000" in joined
    assert "10,500" not in joined, "the old rate must not survive a re-sync"


async def test_a_deleted_room_type_disappears(db, hotel):
    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    room = (
        await db.execute(
            select(RoomType).where(
                RoomType.hotel_id == hotel.id, RoomType.name == "Family Cottage"
            )
        )
    ).scalar_one()
    await db.delete(room)
    await db.flush()
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    assert not any("Family Cottage" in t for t in await _titles(db, hotel.id))


async def test_a_hotel_with_no_room_types_still_syncs_policies(db, hotel):
    """The original Rupakot state. Must not error on an empty rate card."""
    await _setup(db, hotel, rooms=False)
    results = await ingest.sync_hotel_setup(db, hotel_id=hotel.id)
    assert results
    assert any("Cancellation" in t for t in await _titles(db, hotel.id))


async def test_the_sync_is_scoped_to_the_hotel(db, hotel):
    from app.platform.models import Hotel

    other = Hotel(name="Someone Else's Property")
    db.add(other)
    await db.flush()
    db.add(
        RoomType(
            hotel_id=other.id, name="Their Room", base_rate=Decimal("999"),
            max_occupancy=2, amenities=[],
        )
    )
    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    assert not any("Their Room" in t for t in await _titles(db, hotel.id))


# --- the rate card as a whole --------------------------------------------
#
# Measured with only per-room documents present: "What is your starting
# rate?" and "What is the cheapest room you have?" were REFUSED. Every
# rate was in the knowledge base, but no single passage said "cheapest",
# and the grounding prompt correctly declines to rank across passages
# rather than compare. Loosening that instruction was not an option - it
# is what holds hallucination down - so the comparison is stated as a fact.


async def test_a_starting_rate_question_is_answerable(db, hotel):
    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="What is your starting rate?", limit=3
    )
    joined = " ".join(h.chunk_text for h in hits)
    assert "10,500" in joined
    assert "start" in joined.lower() or "lowest" in joined.lower()


async def test_the_cheapest_room_is_named(db, hotel):
    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="What is the cheapest room you have?",
        limit=3,
    )
    joined = " ".join(h.chunk_text for h in hits)
    assert "lowest nightly rate is the Deluxe Room" in joined


async def test_the_summary_ranks_by_rate_not_by_insertion_order(db, hotel):
    from decimal import Decimal as D

    from app.platform.models import RoomType as RT

    rooms = [
        RT(name="Expensive", base_rate=D("22000"), max_occupancy=2, amenities=[]),
        RT(name="Cheap", base_rate=D("5000"), max_occupancy=1, amenities=[]),
        RT(name="Middle", base_rate=D("9000"), max_occupancy=6, amenities=[]),
    ]
    body = ingest._rate_summary_document(rooms, "NPR")
    assert "lowest nightly rate is the Cheap at NPR 5,000" in body
    assert "highest is the Expensive at NPR 22,000" in body
    # Largest is by occupancy, not by price - a cheap room can sleep more.
    assert "largest room type is the Middle" in body
    assert body.index("Cheap at NPR 5,000") < body.index("Expensive at NPR 22,000")


async def test_a_single_room_type_does_not_claim_a_range(db, hotel):
    """'The lowest is X and the highest is X' reads as a mistake."""
    from decimal import Decimal as D

    from app.platform.models import RoomType as RT

    body = ingest._rate_summary_document(
        [RT(name="Only Room", base_rate=D("7000"), max_occupancy=2, amenities=[])],
        "NPR",
    )
    assert "There is one room type" in body
    assert "highest" not in body


async def test_no_summary_when_there_are_no_room_types(db, hotel):
    """Rupakot's state before this data arrived. An empty summary document
    would fail ingestion with 'produced no chunks'."""
    await _setup(db, hotel, rooms=False)
    results = await ingest.sync_hotel_setup(db, hotel_id=hotel.id)
    assert not any("overview" in r.title.lower() for r in results)


async def test_the_summary_is_regenerated_from_the_rows(db, hotel):
    """It must not drift from the per-room documents when a rate changes -
    which is the whole reason it is derived rather than hand-written."""
    from decimal import Decimal as D

    await _setup(db, hotel)
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    room = (
        await db.execute(
            select(RoomType).where(
                RoomType.hotel_id == hotel.id, RoomType.name == "Deluxe Room"
            )
        )
    ).scalar_one()
    room.base_rate = D("3000.00")
    await db.flush()
    await ingest.sync_hotel_setup(db, hotel_id=hotel.id)

    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="cheapest room starting rate", limit=4
    )
    joined = " ".join(h.chunk_text for h in hits)
    assert "NPR 3,000" in joined
    assert "10,500" not in joined
