"""
Retrieval: pgvector similarity search over a hotel's knowledge chunks.

Every query is scoped by hotel_id. That filter is not optional and not a
caller's responsibility to remember - it is applied here, on the
denormalised column, so tenant isolation (NFR-3) holds even if a caller
forgets.
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import model_router
from app.modules.receptionist.models import KnowledgeChunk, KnowledgeDocument


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    document_title: str
    source_type: str
    chunk_text: str
    score: float
    token_count: int


async def search(
    db: AsyncSession,
    *,
    hotel_id: int,
    query: str,
    limit: int = 5,
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    """Return the closest chunks to `query`, best first.

    `score` is cosine similarity in [-1, 1], derived as 1 - cosine_distance.
    Higher is more similar.
    """
    if not query.strip():
        return []

    vector = await run_in_threadpool(model_router.embed_query, query)

    distance = KnowledgeChunk.embedding.cosine_distance(vector).label("distance")
    stmt = (
        select(
            KnowledgeChunk,
            distance,
            KnowledgeDocument.title,
            KnowledgeDocument.source_type,
        )
        .join(
            KnowledgeDocument,
            KnowledgeDocument.id == KnowledgeChunk.knowledge_document_id,
        )
        .where(KnowledgeChunk.hotel_id == hotel_id)
        .order_by(distance)
        .limit(limit)
    )

    rows = (await db.execute(stmt)).all()
    results = [
        RetrievedChunk(
            chunk_id=chunk.id,
            document_id=chunk.knowledge_document_id,
            document_title=title,
            source_type=source_type.value
            if hasattr(source_type, "value")
            else str(source_type),
            chunk_text=chunk.chunk_text,
            score=round(1.0 - float(dist), 4),
            token_count=chunk.token_count,
        )
        for chunk, dist, title, source_type in rows
    ]
    if min_score is not None:
        results = [r for r in results if r.score >= min_score]
    return results
