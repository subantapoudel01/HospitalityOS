"""
Ingestion: raw text in, embedded chunks in Postgres out.

Embedding is CPU-bound synchronous work (ONNX inference). Running it
directly inside an async request handler would block the event loop for
every other request, so it goes through the threadpool.

Domain-agnostic. Anything that knows what a room or a rate is lives in the
vertical - see the hospitality sync module, which calls ingest_text() to do
the actual chunking and embedding.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fastapi.concurrency import run_in_threadpool
from sqlalchemy.ext.asyncio import AsyncSession

from conversa.core import model_router
from conversa.rag.chunking import chunk_text
from conversa.rag import quality
from conversa.rag.models import (
    EMBEDDING_DIM,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSourceType,
)


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
