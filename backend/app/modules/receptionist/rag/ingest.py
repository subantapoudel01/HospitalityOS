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
from app.platform.models import Hotel, HotelPolicy


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


async def sync_hotel_policies(db: AsyncSession, *, hotel_id: int) -> list[IngestResult]:
    """Pull Slice A hotel_policies rows into the knowledge base.

    Policies are structured data the staff already entered; this makes them
    retrievable without asking anyone to retype them. Re-running replaces
    the previously synced policy documents rather than duplicating them.
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
    return out


async def hotel_exists(db: AsyncSession, hotel_id: int) -> bool:
    result = await db.execute(select(Hotel.id).where(Hotel.id == hotel_id))
    return result.scalar_one_or_none() is not None
