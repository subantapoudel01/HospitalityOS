"""
Knowledge base models — the RAG unit of the receptionist module.

Owned by the receptionist because it exists to answer guest questions, but
it foreign-keys into platform data (hotels). That direction is allowed;
platform must never import from here.
"""
from __future__ import annotations

import enum
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base

# Storage dimension of the `embedding` column.
#
# This MUST match migration 0002. It is deliberately a hardcoded constant
# rather than something read from config: the column type is fixed once the
# table exists, so a provider whose vectors are a different width needs a
# new migration AND a full re-embed of every stored chunk. Ingest validates
# each vector against this so a mismatch fails loudly instead of silently
# storing garbage.
#
# 384 = intfloat/multilingual-e5-small (the default local provider).
# Switching to gemini-embedding-001 means 3072 — see MODEL_SELECTION.md.
EMBEDDING_DIM = 384


class KnowledgeSourceType(str, enum.Enum):
    faq = "faq"
    policy = "policy"
    amenity = "amenity"
    upload = "upload"


class KnowledgeDocument(Base):
    """A source document as the staff member supplied it, before chunking."""

    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    hotel_id: Mapped[int] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[KnowledgeSourceType] = mapped_column(
        Enum(
            KnowledgeSourceType,
            name="knowledge_source_type",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=KnowledgeSourceType.upload,
    )
    raw_content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="KnowledgeChunk.id",
    )


class KnowledgeChunk(Base):
    """One embedded passage. This is what retrieval actually searches."""

    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    knowledge_document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Denormalised from the parent document on purpose: RAG queries filter
    # by hotel on every single search, and doing it without a join keeps
    # latency down (NFR-1) while making tenant isolation enforceable
    # directly on this table (NFR-3).
    hotel_id: Mapped[int] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM), nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    document: Mapped["KnowledgeDocument"] = relationship(back_populates="chunks")
