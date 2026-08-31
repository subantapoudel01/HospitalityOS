"""
Hybrid retrieval: pgvector similarity plus Postgres full-text search.

Every query is scoped by hotel_id. That filter is not optional and not a
caller's responsibility to remember - it is applied here, on the
denormalised column, so tenant isolation (NFR-3) holds even if a caller
forgets. BOTH halves of the hybrid carry it.

WHY HYBRID
----------
Embeddings capture meaning and are poor at rare literal tokens. Measured
against the Rupakot corpus with vector search alone:

    "Do you take cash?"       0.397  Payment policy               correct
    "Can I pay with eSewa?"   0.384  Payment policy               correct
    "Do you accept eSewa?"    0.189  Payment policy               below floor
    "Do you take eSewa?"      0.177  "How long to allow..."       wrong
    "Do you take Visa?"       0.240  "Begnas Lake and Rupa Lake"  wrong

"Do you take" dominates the embedding and the brand name barely registers,
so a lake passage outranks the payment policy. A literal token is exactly
what an inverted index is for.

WHY NOT plainto_tsquery
-----------------------
It ANDs the terms. `plainto_tsquery('english', 'Do you take eSewa?')`
gives `take & esewa`, and the policy says "accept", not "take" - so it
scores ZERO and matches nothing. Verified before building this. The naive
FTS bolt-on would not have fixed the bug it was meant to fix.

So terms are scored INDEPENDENTLY and summed, which is OR semantics with a
per-term weight.

WHY IDF, NOT ts_rank
--------------------
`ts_rank_cd` scores a match on "esewa" the same as a match on "room".
Measured document frequency over the same 37 chunks:

    esewa, visa, fonepay, mastercard, payment, cash    1 chunk each
    resort                                             6
    room, night                                        8
    take                                               0

A term in 1 of 37 chunks is near-proof of relevance; one in 8 is a hint.
Weighting by ln(1 + N/df) encodes that, computed per hotel from that
hotel's own corpus rather than from a hand-maintained list of "important"
words that would go stale the day a new property is added.

HOW THE SCORES COMBINE - the part that matters most
---------------------------------------------------
The obvious move is min-max normalising both scores per query. That would
be a mistake here. `chat_min_score` is an ABSOLUTE threshold, and
per-query normalisation makes every query's best hit 1.0 - so the floor
stops filtering anything and starts admitting the best of a bad set. Given
the floor already separates in-scope from out-of-scope only weakly (see
app/core/config.py), removing what discrimination it has is the wrong
direction.

So both parts stay on an absolute [0, 1] scale and combine with noisy-OR:

    lexical  = sum(idf of matched terms) / sum(idf of all scoring terms)
    combined = 1 - (1 - vector) * (1 - LEXICAL_WEIGHT * lexical)

The property that makes this safe to ship:

    lexical == 0  =>  combined == vector, exactly.

Every query that finds no lexical match behaves bit-for-bit as it did
before this module was rewritten, so the existing floor calibration still
holds for them. Lexical evidence can only RAISE a score, never lower one -
this fixes false negatives and deliberately does not re-rank anything else.

WHAT THIS DOES NOT FIX
----------------------
A brand the hotel has never mentioned. "Do you take Khalti?" has nothing
to match, so it stays on the weak vector path and may still miss. The
right answer there is for the payment policy to be retrieved on meaning
alone, which is a different problem.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.types import Float, String

from app.core import model_router
from app.core.config import settings

#: Terms appearing in no chunk at all are dropped rather than counted in
#: the denominator. Otherwise "Do you take Khalti?" - where nothing
#: matches - would cap every lexical score below 1.0 for no reason.
_MIN_DF = 1


@dataclass
class RetrievedChunk:
    chunk_id: int
    document_id: int
    document_title: str
    source_type: str
    chunk_text: str
    score: float
    token_count: int
    # The two halves, kept for diagnosis. A wrong answer can be traced to
    # "the vector missed it and lexical carried it" or the reverse, which
    # is not recoverable from the combined number alone.
    vector_score: float = 0.0
    lexical_score: float = 0.0


# Tokenise the query with the SAME configuration the stored column uses,
# and count how many of this hotel's chunks contain each term. Tokenising
# in Postgres rather than Python is what keeps query terms and indexed
# lexemes in agreement - a Python tokeniser would drift from the 'english'
# stemmer the moment either changed.
_TERM_STATS = text(
    """
    WITH terms AS (
        SELECT DISTINCT unnest(
            tsvector_to_array(to_tsvector('english', :query))
        ) AS term
    ),
    corpus AS (
        SELECT count(*)::float8 AS n
        FROM knowledge_chunks WHERE hotel_id = :hotel_id
    )
    SELECT
        t.term,
        (
            SELECT count(*) FROM knowledge_chunks k
            WHERE k.hotel_id = :hotel_id
              AND k.search_vector @@ plainto_tsquery('simple', t.term)
        )::float8 AS df,
        corpus.n AS n
    FROM terms t, corpus
    """
)

# One pass over the hotel's chunks producing both scores and the fusion.
# `plainto_tsquery('simple', ...)` on an already-stemmed lexeme returns it
# unchanged - using 'english' here would stem it a second time.
_HYBRID_SEARCH = text(
    """
    WITH scored AS (
        SELECT
            k.id,
            k.knowledge_document_id,
            k.chunk_text,
            k.token_count,
            -- Cosine similarity, clamped: a negative similarity is worse
            -- than no evidence and would invert the noisy-OR below.
            GREATEST(1.0 - (k.embedding <=> CAST(:query_vector AS vector)), 0.0)
                AS vector_score,
            CASE WHEN CAST(:total_weight AS float8) > 0.0 THEN
                COALESCE((
                    SELECT sum(t.weight)
                    FROM unnest(CAST(:terms AS text[]), CAST(:weights AS float8[]))
                         AS t(term, weight)
                    WHERE k.search_vector @@ plainto_tsquery('simple', t.term)
                ), 0.0) / CAST(:total_weight AS float8)
            ELSE 0.0 END AS lexical_score
        FROM knowledge_chunks k
        WHERE k.hotel_id = :hotel_id
    )
    SELECT
        s.id,
        s.knowledge_document_id,
        d.title,
        d.source_type,
        s.chunk_text,
        s.token_count,
        s.vector_score,
        s.lexical_score,
        1.0 - (1.0 - s.vector_score)
            * (1.0 - CAST(:lexical_weight AS float8) * s.lexical_score)
            AS combined
    FROM scored s
    JOIN knowledge_documents d ON d.id = s.knowledge_document_id
    ORDER BY combined DESC
    LIMIT :limit
    """
).bindparams(
    bindparam("terms", type_=ARRAY(String)),
    bindparam("weights", type_=ARRAY(Float)),
    # Explicit Float, and CAST at every use site above. Postgres infers a
    # parameter's type from its first use: `:total_weight > 0` against an
    # integer literal inferred int4, and asyncpg then truncated 3.638 to 3.
    # The lexical score silently came out above 1.0, breaking the bound the
    # whole fusion rests on. Belt and braces on purpose.
    bindparam("total_weight", type_=Float),
    bindparam("lexical_weight", type_=Float),
)


async def _term_weights(
    db: AsyncSession, *, hotel_id: int, query: str
) -> tuple[list[str], list[float], float]:
    """Query terms and their inverse document frequency, for this hotel.

    Returns (terms, weights, total_weight). An empty result - no query
    terms, or none of them appearing anywhere in this hotel's corpus -
    gives total_weight 0, which the search then treats as vector-only.
    """
    rows = (
        await db.execute(_TERM_STATS, {"query": query, "hotel_id": hotel_id})
    ).all()

    terms: list[str] = []
    weights: list[float] = []
    for term, df, n in rows:
        if df < _MIN_DF:
            # In no chunk, so it can never match. Counting it would only
            # depress every lexical score.
            continue
        # ln(1 + N/df): 1-in-37 scores 3.64, 8-in-37 scores 1.72, and a
        # term in every chunk scores ln(2) = 0.69. Never zero, so a common
        # word still counts for a little.
        weights.append(math.log(1.0 + (n / df)))
        terms.append(term)

    return terms, weights, sum(weights)


async def search(
    db: AsyncSession,
    *,
    hotel_id: int,
    query: str,
    limit: int = 5,
    min_score: float | None = None,
) -> list[RetrievedChunk]:
    """Return the best chunks for `query`, best first.

    `score` stays on the cosine scale it has always been on, so
    `chat_min_score` means what it meant before hybrid retrieval existed.
    See the module docstring for why it is not normalised per query.
    """
    if not query.strip():
        return []

    vector = await run_in_threadpool(model_router.embed_query, query)

    if settings.chat_hybrid_retrieval:
        terms, weights, total_weight = await _term_weights(
            db, hotel_id=hotel_id, query=query
        )
    else:
        terms, weights, total_weight = [], [], 0.0

    rows = (
        await db.execute(
            _HYBRID_SEARCH,
            {
                # pgvector accepts the literal '[0.1,0.2,...]' form.
                "query_vector": "[" + ",".join(str(v) for v in vector) + "]",
                "hotel_id": hotel_id,
                "terms": terms,
                "weights": weights,
                "total_weight": total_weight,
                "lexical_weight": settings.chat_lexical_weight,
                "limit": limit,
            },
        )
    ).all()

    results = [
        RetrievedChunk(
            chunk_id=row.id,
            document_id=row.knowledge_document_id,
            document_title=row.title,
            source_type=(
                row.source_type.value
                if hasattr(row.source_type, "value")
                else str(row.source_type)
            ),
            chunk_text=row.chunk_text,
            score=round(float(row.combined), 4),
            token_count=row.token_count,
            vector_score=round(float(row.vector_score), 4),
            lexical_score=round(float(row.lexical_score), 4),
        )
        for row in rows
    ]
    if min_score is not None:
        results = [r for r in results if r.score >= min_score]
    return results
