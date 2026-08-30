"""
Continuous retrieval evaluation (Stage 4).

Runs the real embedding model against real pgvector. Nothing is mocked,
because a mocked embedding proves only that the SQL compiles.
"""
from __future__ import annotations

import pytest

from app.modules.receptionist.models import KnowledgeSourceType
from app.modules.receptionist.rag import ingest, retrieval
from tests.llm_eval.golden_set import (
    CASES,
    DOCUMENTS,
    KNOWN_GAPS,
    MIN_RECALL_AT_3,
    MIN_TOP1_ACCURACY,
    RETRIEVAL_K,
)

pytestmark = pytest.mark.asyncio


async def _load_corpus(db, hotel_id: int) -> None:
    for title, source_type, body in DOCUMENTS:
        await ingest.ingest_text(
            db,
            hotel_id=hotel_id,
            title=title,
            raw_content=body,
            source_type=KnowledgeSourceType(source_type),
        )
    await db.flush()


async def test_corpus_ingests_and_chunks(db, hotel):
    await _load_corpus(db, hotel.id)
    hits = await retrieval.search(db, hotel_id=hotel.id, query="breakfast", limit=50)
    # 6 documents, several of which split into multiple paragraph chunks.
    assert len(hits) >= len(DOCUMENTS), "some documents produced no chunks"


async def test_retrieval_golden_set(db, hotel):
    """Recall@3 must be perfect; top-1 is reported and floored."""
    await _load_corpus(db, hotel.id)

    top1_hits = 0
    recall_hits = 0
    gated = 0
    failures: list[str] = []
    gap_status: list[str] = []

    for question, expected, lang in CASES:
        results = await retrieval.search(
            db, hotel_id=hotel.id, query=question, limit=RETRIEVAL_K
        )
        texts = [r.chunk_text for r in results]
        in_top1 = bool(texts) and expected in texts[0]
        in_topk = any(expected in t for t in texts)

        if question in KNOWN_GAPS:
            gap_status.append(
                f"  known gap [{lang}] {question!r}: "
                + ("NOW PASSING - remove from KNOWN_GAPS" if in_topk else "still failing")
            )
            continue

        gated += 1
        top1_hits += in_top1
        recall_hits += in_topk
        if not in_topk:
            failures.append(
                f"[{lang}] {question!r} -> expected {expected!r}, got "
                + " | ".join(f"{r.score:.2f} {r.chunk_text[:40]!r}" for r in results)
            )

    total = gated
    recall = recall_hits / total
    top1 = top1_hits / total
    if gap_status:
        print("\n" + "\n".join(gap_status))
    print(f"\nrecall@{RETRIEVAL_K}: {recall:.0%} ({recall_hits}/{total})")
    print(f"top-1 accuracy: {top1:.0%} ({top1_hits}/{total})")

    assert recall >= MIN_RECALL_AT_3, (
        f"recall@{RETRIEVAL_K} {recall:.0%} below {MIN_RECALL_AT_3:.0%}\n"
        + "\n".join(failures)
    )
    assert top1 >= MIN_TOP1_ACCURACY, f"top-1 {top1:.0%} below {MIN_TOP1_ACCURACY:.0%}"


async def test_multilingual_cases_all_retrieve(db, hotel):
    """Nepali and Romanized Nepali must not regress relative to English."""
    await _load_corpus(db, hotel.id)

    by_lang: dict[str, list[bool]] = {}
    for question, expected, lang in CASES:
        results = await retrieval.search(
            db, hotel_id=hotel.id, query=question, limit=RETRIEVAL_K
        )
        if question in KNOWN_GAPS:
            continue
        by_lang.setdefault(lang, []).append(
            any(expected in r.chunk_text for r in results)
        )

    for lang, outcomes in sorted(by_lang.items()):
        rate = sum(outcomes) / len(outcomes)
        print(f"{lang:16} recall@{RETRIEVAL_K} {rate:.0%} ({len(outcomes)} cases)")
        assert rate == 1.0, f"{lang} retrieval regressed: {rate:.0%}"


async def test_tenant_isolation(db, hotel):
    """A hotel must never retrieve another hotel's chunks."""
    from app.platform.models import Hotel

    await _load_corpus(db, hotel.id)
    other = Hotel(name="Someone Else's Property")
    db.add(other)
    await db.flush()

    hits = await retrieval.search(
        db, hotel_id=other.id, query="What time is check-out?", limit=RETRIEVAL_K
    )
    assert hits == [], "tenant leak: another hotel's chunks were returned"


async def test_empty_query_returns_nothing(db, hotel):
    await _load_corpus(db, hotel.id)
    assert await retrieval.search(db, hotel_id=hotel.id, query="   ") == []


async def test_scores_are_ordered_and_bounded(db, hotel):
    await _load_corpus(db, hotel.id)
    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="Where can I eat dinner?", limit=5
    )
    assert hits, "expected some hits"
    scores = [h.score for h in hits]
    assert scores == sorted(scores, reverse=True), "results not ordered by similarity"
    assert all(-1.0001 <= s <= 1.0001 for s in scores), f"scores out of range: {scores}"


async def test_min_score_filters(db, hotel):
    await _load_corpus(db, hotel.id)
    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="Where can I eat dinner?", limit=5, min_score=0.99
    )
    assert hits == [], "min_score did not filter weak matches"
