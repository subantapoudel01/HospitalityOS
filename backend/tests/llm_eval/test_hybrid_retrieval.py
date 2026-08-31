"""
Hybrid retrieval: pgvector similarity fused with Postgres full-text search.

The bug: embeddings are poor at rare literal tokens. "Do you take eSewa?"
scored 0.177 and returned a passage about lakes, while "Do you take cash?"
scored 0.397 and answered correctly - the difference being that "cash" is
an ordinary word and "eSewa" is a brand name the model barely represents.

Most of this file guards the SAFETY properties rather than the fix. The
fusion can only be trusted if its output stays on the same absolute scale
`chat_min_score` was calibrated against, so the bounds and the
"no lexical match changes nothing" invariant matter more than any single
retrieval win.
"""
from __future__ import annotations

import pytest

from app.core import model_router
from app.core.config import settings
from app.modules.receptionist.models import KnowledgeSourceType
from app.modules.receptionist.rag import ingest, retrieval

pytestmark = pytest.mark.asyncio

CORPUS = [
    ("Payment policy", "policy",
     "Payment policy: We accept cash in NPR, major credit/debit cards "
     "(Visa, MasterCard), and digital payments via eSewa and Fonepay. "
     "A 20% advance deposit is required to confirm bookings."),
    ("Checkin Checkout policy", "policy",
     "Checkin Checkout policy: Check-in is from 2 PM. Check-out is by 11 AM."),
    ("Begnas Lake and Rupa Lake", "amenity",
     "Begnas Lake and Rupa Lake sit at the base of the hills. They are "
     "recommended for quiet wooden boat rides and traditional fish dining."),
    ("Dining hours", "amenity",
     "Breakfast is served from 7:00 AM to 9:30 AM.\n\n"
     "Dinner is served from 7:00 PM to 10:00 PM."),
    ("Resort facilities", "amenity",
     "Wellness facilities include an outdoor infinity swimming pool, a "
     "luxury spa, a hot tub and dedicated yoga spaces."),
]


@pytest.fixture(autouse=True)
def _hybrid_on(monkeypatch):
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")
    monkeypatch.setattr(settings, "chat_hybrid_retrieval", True)
    monkeypatch.setattr(settings, "chat_lexical_weight", 0.5)


async def _seed(db, hotel):
    for title, source, body in CORPUS:
        await ingest.ingest_text(
            db, hotel_id=hotel.id, title=title, raw_content=body,
            source_type=KnowledgeSourceType(source),
        )
    await db.flush()


async def _top(db, hotel, query, limit=1):
    hits = await retrieval.search(
        db, hotel_id=hotel.id, query=query, limit=limit
    )
    return hits[0] if hits else None


# --- the bug this exists to fix -----------------------------------------


@pytest.mark.parametrize(
    "query",
    [
        "Do you take eSewa?",
        "Do you accept eSewa?",
        "Do you take Visa?",
        "Can I pay by Fonepay?",
        "Do you accept MasterCard?",
    ],
)
async def test_brand_names_find_the_payment_policy(db, hotel, query):
    """Every one of these used to miss or land below the floor.

    "Do you take Visa?" was the worst: 0.240, top hit "Begnas Lake and
    Rupa Lake". A lake passage outranking the payment policy for a card
    question is the whole motivation for this module.
    """
    await _seed(db, hotel)
    hit = await _top(db, hotel, query)

    assert hit is not None
    assert hit.document_title == "Payment policy", (
        f"{query!r} returned {hit.document_title!r}"
    )
    assert hit.score >= settings.chat_min_score, "must clear the floor"


async def test_the_lexical_half_is_what_carries_it(db, hotel):
    """Not an accident of a better embedding: the vector score alone is
    still below the floor, and lexical evidence is what lifts it."""
    await _seed(db, hotel)
    hit = await _top(db, hotel, "Do you take eSewa?")

    assert hit.vector_score < settings.chat_min_score
    assert hit.lexical_score > 0.9
    assert hit.score > settings.chat_min_score


# --- the safety properties ----------------------------------------------


async def test_no_lexical_match_leaves_the_score_exactly_unchanged(db, hotel):
    """THE invariant.

    `combined = 1 - (1 - v)(1 - w*l)` collapses to exactly `v` when
    `l == 0`. That is what lets hybrid retrieval ship without
    recalibrating chat_min_score: every query the lexical side cannot
    help behaves bit-for-bit as it did before.
    """
    await _seed(db, hotel)

    # "helipad" and "zzz" appear nowhere in the corpus, so nothing scores.
    for query in ("Do you have a helipad?", "zzzqqq"):
        hits = await retrieval.search(db, hotel_id=hotel.id, query=query, limit=5)
        for hit in hits:
            assert hit.lexical_score == 0.0
            assert hit.score == hit.vector_score, (
                "a query with no lexical match must not move at all"
            )


async def test_the_lexical_score_is_bounded(db, hotel):
    """Regression: it was NOT.

    `:total_weight` was compared against the integer literal 0, so
    Postgres inferred int4 for the parameter and asyncpg truncated the
    float - 3.638 arrived as 3. Lexical scores came back at 1.21 and 1.49,
    silently breaking the [0,1] bound the whole fusion rests on, and
    inflating combined scores past what the floor was calibrated for.
    """
    await _seed(db, hotel)

    for query in [
        "Do you take eSewa?",                       # one scoring term
        "Is there a casino on site?",               # one scoring, one absent
        "What payment methods do you accept?",      # several scoring terms
        "breakfast dinner pool spa lake payment",   # many, mixed rarity
        "check out time and breakfast and payment", # stopwords mixed in
    ]:
        hits = await retrieval.search(db, hotel_id=hotel.id, query=query, limit=5)
        for hit in hits:
            assert 0.0 <= hit.lexical_score <= 1.0, (
                f"{query!r}: lexical {hit.lexical_score} out of range"
            )
            assert 0.0 <= hit.score <= 1.0, (
                f"{query!r}: combined {hit.score} out of range"
            )


async def test_lexical_evidence_only_ever_raises(db, hotel):
    """Fixing false negatives, not re-ranking. A chunk's combined score
    must never fall below what the vector alone would have given it -
    otherwise this change could bury a result that used to be found."""
    await _seed(db, hotel)

    for query in ["Do you take eSewa?", "breakfast", "What time is check-out?"]:
        hits = await retrieval.search(db, hotel_id=hotel.id, query=query, limit=5)
        for hit in hits:
            assert hit.score >= hit.vector_score - 1e-9


async def test_results_stay_sorted_by_the_combined_score(db, hotel):
    await _seed(db, hotel)
    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="Do you take eSewa?", limit=5
    )
    assert hits == sorted(hits, key=lambda h: h.score, reverse=True)


# --- what it deliberately does NOT do -----------------------------------


async def test_an_unmentioned_brand_is_not_invented(db, hotel):
    """Khalti is a real payment provider this hotel does not list.

    Nothing matches it lexically, so the query stays on the vector path
    and the honest outcome is that it is not found. A hybrid that
    "helpfully" surfaced the payment policy here would be teaching the
    model to answer a question the corpus cannot support.
    """
    await _seed(db, hotel)
    hit = await _top(db, hotel, "Do you take Khalti?")
    assert hit.lexical_score == 0.0
    assert hit.score == hit.vector_score


async def test_a_common_word_does_not_outrank_a_rare_one(db, hotel):
    """IDF, not raw ts_rank. ts_rank_cd scores a match on "esewa" the same
    as a match on "and", which is the reason this weighting exists.

    The frequencies are constructed here rather than assumed: an earlier
    version of this test compared "esewa" to "breakfast" and both happened
    to sit in exactly one chunk of the fixture corpus, so it asserted a
    difference that was correctly absent.
    """
    await _seed(db, hotel)
    # "riverside" now appears in three separate chunks (blank lines split
    # them), against "esewa" in one.
    await ingest.ingest_text(
        db, hotel_id=hotel.id, title="Riverside walks",
        raw_content=(
            "The riverside path is pleasant in the morning.\n\n"
            "A riverside bench sits near the second bend.\n\n"
            "The riverside route returns past the gate."
        ),
        source_type=KnowledgeSourceType.amenity,
    )
    await db.flush()

    terms, weights, _ = await retrieval._term_weights(
        db, hotel_id=hotel.id, query="esewa riverside"
    )
    by_term = dict(zip(terms, weights))
    # "riversid", not "riverside": the english config STEMS, which is
    # exactly why the query is tokenised by Postgres rather than by us -
    # a Python tokeniser would look up a key the index does not hold.
    # Brand names are unaffected; "esewa" has nothing to stem.
    assert "esewa" in by_term and "riversid" in by_term
    assert by_term["esewa"] > by_term["riversid"], (
        "a term in one chunk must outweigh one in three"
    )


async def test_terms_absent_from_the_corpus_are_dropped(db, hotel):
    """Counting them in the denominator would cap every lexical score
    below 1.0 for no reason - 'take' appears in no chunk at all."""
    await _seed(db, hotel)
    terms, _, _ = await retrieval._term_weights(
        db, hotel_id=hotel.id, query="Do you take Khalti?"
    )
    assert "khalti" not in terms
    assert "take" not in terms


# --- the rest of the pipeline is unaffected -----------------------------


async def test_ordinary_questions_still_work(db, hotel):
    await _seed(db, hotel)

    assert (await _top(db, hotel, "What time is check-out?")).document_title == (
        "Checkin Checkout policy"
    )
    assert (await _top(db, hotel, "When is breakfast served?")).document_title == (
        "Dining hours"
    )
    assert (await _top(db, hotel, "Do you have a swimming pool?")).document_title == (
        "Resort facilities"
    )


async def test_devanagari_is_unaffected(db, hotel):
    """The 'english' text search config leaves Devanagari tokens alone -
    verified before choosing it - so Slice D must be untouched."""
    await _seed(db, hotel)
    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="चेक-आउट कति बजे हो?", limit=3
    )
    assert hits
    for hit in hits:
        assert 0.0 <= hit.score <= 1.0


async def test_an_empty_query_returns_nothing(db, hotel):
    await _seed(db, hotel)
    assert await retrieval.search(db, hotel_id=hotel.id, query="   ") == []


async def test_a_stopword_only_query_does_not_divide_by_zero(db, hotel):
    """'the and of' tokenises to nothing under the english config, so
    total_weight is 0 and the CASE guard has to hold."""
    await _seed(db, hotel)
    hits = await retrieval.search(db, hotel_id=hotel.id, query="the and of", limit=3)
    for hit in hits:
        assert hit.lexical_score == 0.0
        assert hit.score == hit.vector_score


async def test_min_score_still_filters(db, hotel):
    await _seed(db, hotel)
    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="Do you have a helipad?", limit=5,
        min_score=0.9,
    )
    assert hits == []


# --- tenant isolation, on BOTH halves -----------------------------------


async def test_the_lexical_half_is_scoped_to_the_hotel(db, hotel):
    """The obvious way to get hybrid search wrong: remember hotel_id on
    the vector query and forget it on the lexical one, so a rare brand
    name pulls another property's chunk straight to the top."""
    from app.platform.models import Hotel

    other = Hotel(name="Someone Else's Property")
    db.add(other)
    await db.flush()
    await ingest.ingest_text(
        db, hotel_id=other.id, title="Their payment policy",
        raw_content=(
            "Their payment policy: we accept eSewa, Visa and Khalti only."
        ),
        source_type=KnowledgeSourceType.policy,
    )
    await _seed(db, hotel)

    hits = await retrieval.search(
        db, hotel_id=hotel.id, query="Do you take eSewa?", limit=10
    )
    assert hits
    assert all(h.document_title != "Their payment policy" for h in hits)

    # ...and the term statistics must not leak across either: Khalti
    # exists only in the other hotel's corpus.
    terms, _, _ = await retrieval._term_weights(
        db, hotel_id=hotel.id, query="Do you take Khalti?"
    )
    assert "khalti" not in terms


# --- the off switch ------------------------------------------------------


async def test_hybrid_can_be_turned_off(db, hotel, monkeypatch):
    """Falls back to pure vector search. The two are score-compatible, so
    the floor needs no adjustment either way."""
    await _seed(db, hotel)
    monkeypatch.setattr(settings, "chat_hybrid_retrieval", False)

    hit = await _top(db, hotel, "Do you take eSewa?")
    assert hit.lexical_score == 0.0
    assert hit.score == hit.vector_score
    assert hit.score < settings.chat_min_score, (
        "the original bug should reappear with the feature off"
    )


async def test_the_lexical_weight_is_honoured(db, hotel, monkeypatch):
    await _seed(db, hotel)

    monkeypatch.setattr(settings, "chat_lexical_weight", 0.0)
    none = await _top(db, hotel, "Do you take eSewa?")
    assert none.score == none.vector_score

    monkeypatch.setattr(settings, "chat_lexical_weight", 0.9)
    strong = await _top(db, hotel, "Do you take eSewa?")
    assert strong.score > none.score
