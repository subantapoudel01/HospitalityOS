"""
Ingestion: raw text in, embedded chunks in Postgres out.

Embedding is CPU-bound synchronous work (ONNX inference). Running it
directly inside an async request handler would block the event loop for
every other request, so it goes through the threadpool.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import model_router
from app.modules.receptionist.models import (
    EMBEDDING_DIM,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSourceType,
)
from app.modules.receptionist.rag.chunking import chunk_text
from app.modules.receptionist.rag import quality
from app.platform.models import Hotel, HotelPolicy, RoomType


@dataclass
class IngestResult:
    document_id: int
    title: str
    chunks_created: int
    # Content the assistant will struggle to answer from. Advisory only -
    # the document is stored either way. See rag/quality.py.
    warnings: list[dict] = field(default_factory=list)


async def _embed(texts: list[str]) -> list[list[float]]:
    vectors = await run_in_threadpool(model_router.embed_documents, texts)
    # Belt and braces: the router already checks, but a mismatch here would
    # surface as an opaque database error rather than a fixable message.
    for v in vectors:
        if len(v) != EMBEDDING_DIM:
            raise ValueError(
                f"Embedding width {len(v)} does not match the "
                f"knowledge_chunks column ({EMBEDDING_DIM})."
            )
    return vectors


async def ingest_text(
    db: AsyncSession,
    *,
    hotel_id: int,
    title: str,
    raw_content: str,
    source_type: KnowledgeSourceType = KnowledgeSourceType.upload,
) -> IngestResult:
    """Chunk, embed and store one document. Caller commits."""
    chunks = chunk_text(raw_content)
    if not chunks:
        raise ValueError("Document produced no chunks - is the content empty?")

    vectors = await _embed([c.text for c in chunks])

    document = KnowledgeDocument(
        hotel_id=hotel_id,
        title=title,
        source_type=source_type,
        raw_content=raw_content,
    )
    document.chunks = [
        KnowledgeChunk(
            hotel_id=hotel_id,
            chunk_text=chunk.text,
            embedding=vector,
            token_count=chunk.token_count,
        )
        for chunk, vector in zip(chunks, vectors)
    ]
    db.add(document)
    await db.flush()
    return IngestResult(
        document_id=document.id,
        title=document.title,
        chunks_created=len(chunks),
        warnings=[
            {"code": w.code, "severity": w.severity, "message": w.message}
            for w in quality.assess(raw_content, label=f"{title!r}")
        ],
    )


def _room_type_document(room: RoomType, currency: str) -> str:
    """One room type, written the way a receptionist would say it.

    Prose rather than a field dump, because retrieval matches on meaning:
    "Deluxe Room | 10500 | 2 | AC,WiFi" scores poorly against "how much is
    a room for two people", while a sentence containing those words scores
    well.

    Only what staff actually entered. A room with no amenities listed gets
    no amenities sentence rather than an invented one.
    """
    # Thousands separators, because "NPR 10,500" is how a rate is read
    # aloud and how a guest will type it back. Paise only when the stored
    # value actually has them - no trailing ".00" on a whole-rupee rate.
    rate = (
        f"{room.base_rate:,.0f}"
        if room.base_rate == room.base_rate.to_integral_value()
        else f"{room.base_rate:,.2f}"
    )
    guests = "1 guest" if room.max_occupancy == 1 else f"{room.max_occupancy} guests"

    lines = [
        f"The {room.name} costs {currency} {rate} per night and sleeps up to "
        f"{guests}."
    ]
    if room.description:
        lines.append(room.description.strip())
    if room.amenities:
        lines.append(
            f"The {room.name} includes: " + ", ".join(room.amenities) + "."
        )
    # One paragraph per fact: chunking splits on blank lines, so this keeps
    # the rate and the amenity list separately retrievable.
    return "\n\n".join(lines)


def _rate_summary_document(rooms: list[RoomType], currency: str) -> str:
    """The rate card as a whole, including which room is cheapest.

    Every fact here is already in the per-room documents, so this looks
    redundant. It is not, and the reason is worth stating: retrieval
    matches passages, and the assistant will not do arithmetic or ranking
    across several of them. Measured with only per-room documents present,
    "What is your starting rate?" and "What is the cheapest room you have?"
    were REFUSED - the rates were all sitting in the knowledge base and no
    single passage said "cheapest", so the grounding prompt correctly
    declined rather than compare.

    The alternative was loosening the grounding instruction, which is the
    one thing holding hallucination down. Stating the comparison as a fact
    the hotel asserts is cheaper and safer.

    Derived from the same RoomType rows on every sync, so it cannot drift
    away from the per-room documents the way a hand-written summary would.
    """
    if not rooms:
        return ""

    def fmt(room: RoomType) -> str:
        return (
            f"{currency} {room.base_rate:,.0f}"
            if room.base_rate == room.base_rate.to_integral_value()
            else f"{currency} {room.base_rate:,.2f}"
        )

    by_rate = sorted(rooms, key=lambda r: r.base_rate)
    listing = "; ".join(
        f"{r.name} at {fmt(r)} per night for up to {r.max_occupancy} "
        f"{'guest' if r.max_occupancy == 1 else 'guests'}"
        for r in by_rate
    )

    lines = [f"Room types and nightly rates: {listing}."]

    cheapest, dearest = by_rate[0], by_rate[-1]
    if cheapest.name == dearest.name:
        lines.append(
            f"There is one room type, the {cheapest.name}, at "
            f"{fmt(cheapest)} per night."
        )
    else:
        lines.append(
            f"The lowest nightly rate is the {cheapest.name} at "
            f"{fmt(cheapest)}. The highest is the {dearest.name} at "
            f"{fmt(dearest)}. Rates start from {fmt(cheapest)} per night."
        )

    largest = max(rooms, key=lambda r: r.max_occupancy)
    lines.append(
        f"The largest room type is the {largest.name}, which sleeps up to "
        f"{largest.max_occupancy} "
        f"{'guest' if largest.max_occupancy == 1 else 'guests'}."
    )
    return "\n\n".join(lines)


async def sync_hotel_setup(db: AsyncSession, *, hotel_id: int) -> list[IngestResult]:
    """Pull the Slice A setup data - policies AND room types - into the
    knowledge base.

    Both are structured data staff already entered at /setup; this makes
    them retrievable without asking anyone to retype them. Re-running
    replaces the previously synced documents rather than duplicating them.

    Room types were originally left out, which meant a rate card entered at
    /setup was visible in the form and invisible to guests: the assistant
    could not answer "how much is a Deluxe Room" from data the hotel had
    already given it. Syncing from the same rows keeps /setup the single
    source of truth, so editing a rate there and re-syncing updates what
    guests are told - rather than the rate living in a hand-written
    document that silently drifts.
    """
    existing = await db.execute(
        select(KnowledgeDocument).where(
            KnowledgeDocument.hotel_id == hotel_id,
            KnowledgeDocument.source_type == KnowledgeSourceType.policy,
        )
    )
    for doc in existing.scalars().all():
        await db.delete(doc)
    await db.flush()

    result = await db.execute(
        select(HotelPolicy)
        .where(HotelPolicy.hotel_id == hotel_id)
        .order_by(HotelPolicy.id)
    )
    policies = list(result.scalars().all())

    out: list[IngestResult] = []
    for policy in policies:
        label = policy.category.value.replace("_", " ").title()
        # Prefix the category so the chunk carries its own context: a bare
        # "Check-in 2 PM" retrieves far better when the passage says what
        # kind of policy it is.
        body = f"{label} policy: {policy.content_text}"
        out.append(
            await ingest_text(
                db,
                hotel_id=hotel_id,
                title=f"{label} policy",
                raw_content=body,
                source_type=KnowledgeSourceType.policy,
            )
        )

    hotel = (
        await db.execute(select(Hotel).where(Hotel.id == hotel_id))
    ).scalar_one_or_none()
    currency = (hotel.currency if hotel else None) or "NPR"

    rooms = list(
        (
            await db.execute(
                select(RoomType)
                .where(RoomType.hotel_id == hotel_id)
                .order_by(RoomType.id)
            )
        ).scalars().all()
    )
    for room in rooms:
        out.append(
            await ingest_text(
                db,
                hotel_id=hotel_id,
                title=f"{room.name} - rate and details",
                raw_content=_room_type_document(room, currency),
                source_type=KnowledgeSourceType.policy,
            )
        )

    # The rate card as a whole. See _rate_summary_document for why this
    # deliberate redundancy exists.
    if rooms:
        out.append(
            await ingest_text(
                db,
                hotel_id=hotel_id,
                title="Room types and rates overview",
                raw_content=_rate_summary_document(rooms, currency),
                source_type=KnowledgeSourceType.policy,
            )
        )

    return out


# Kept so existing callers and scripts do not break. The name understated
# what it did even before room types were added - it always replaced the
# whole synced set.
sync_hotel_policies = sync_hotel_setup


async def hotel_exists(db: AsyncSession, hotel_id: int) -> bool:
    result = await db.execute(select(Hotel.id).where(Hotel.id == hotel_id))
    return result.scalar_one_or_none() is not None
