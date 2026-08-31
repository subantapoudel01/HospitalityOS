"""
Receptionist module routes.

SECURITY NOTE: like the platform API, these are unauthenticated and take
hotel_id from the request body. Retrieval is still scoped by hotel_id in
retrieval.search(), so one hotel's chunks can never leak into another's
results - but nothing yet stops a caller from naming someone else's hotel
id. Close that with the same session lookup when auth lands (NFR-3).
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import model_router
from app.core.auth import Principal, require_staff, require_staff_token
from app.core.db import get_db
from app.modules.receptionist import schemas
from app.modules.receptionist.models import (
    BookingInquiry,
    KnowledgeChunk,
    KnowledgeDocument,
)
from app.modules.receptionist.rag import ingest, quality, retrieval
from app.modules.receptionist.models import ConversationStatus, InquiryStatus
from app.modules.receptionist.services import conversation as convo
from app.modules.receptionist.services import export
from app.modules.receptionist.services import staff as staff_svc

router = APIRouter(prefix="/receptionist", tags=["receptionist"])


@router.get("/status")
async def status_():
    """Confirms the receptionist module is mounted into the platform app."""
    return {"module": "receptionist", "status": "ok"}


async def _require_hotel(db: AsyncSession, hotel_id: int) -> None:
    if not await ingest.hotel_exists(db, hotel_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hotel {hotel_id} not found",
        )


@router.post(
    "/knowledge/documents",
    response_model=schemas.IngestOut,
    status_code=status.HTTP_201_CREATED,
)
async def ingest_document(
    payload: schemas.DocumentIn, db: AsyncSession = Depends(get_db)
):
    """Chunk, embed and store a piece of text (FAQ, restaurant info, ...)."""
    await _require_hotel(db, payload.hotel_id)
    try:
        result = await ingest.ingest_text(
            db,
            hotel_id=payload.hotel_id,
            title=payload.title,
            raw_content=payload.raw_content,
            source_type=payload.source_type,
        )
    except (ValueError, model_router.EmbeddingError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await db.commit()
    return schemas.IngestOut(**result.__dict__)


@router.get("/knowledge/documents", response_model=list[schemas.DocumentOut])
async def list_documents(
    hotel_id: int = Query(...), db: AsyncSession = Depends(get_db)
):
    chunk_count = (
        select(
            KnowledgeChunk.knowledge_document_id.label("doc_id"),
            func.count(KnowledgeChunk.id).label("n"),
        )
        .group_by(KnowledgeChunk.knowledge_document_id)
        .subquery()
    )
    stmt = (
        select(KnowledgeDocument, func.coalesce(chunk_count.c.n, 0))
        .outerjoin(chunk_count, chunk_count.c.doc_id == KnowledgeDocument.id)
        .where(KnowledgeDocument.hotel_id == hotel_id)
        .order_by(KnowledgeDocument.id)
    )
    rows = (await db.execute(stmt)).all()
    return [
        schemas.DocumentOut(
            id=doc.id,
            hotel_id=doc.hotel_id,
            title=doc.title,
            source_type=doc.source_type,
            created_at=doc.created_at,
            chunk_count=n,
        )
        for doc, n in rows
    ]


@router.delete(
    "/knowledge/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_document(document_id: int, db: AsyncSession = Depends(get_db)):
    doc = await db.get(KnowledgeDocument, document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Document {document_id} not found",
        )
    await db.delete(doc)
    await db.commit()


@router.post("/knowledge/sync-policies", response_model=schemas.SyncPoliciesOut)
async def sync_policies(
    payload: schemas.SyncPoliciesIn, db: AsyncSession = Depends(get_db)
):
    """Ingest the hotel's saved setup data (Slice A) into the knowledge base.

    Policies AND room types. The path keeps its name so existing callers
    and scripts do not break, but it has always replaced the whole synced
    set rather than only policies.
    """
    await _require_hotel(db, payload.hotel_id)
    try:
        results = await ingest.sync_hotel_setup(db, hotel_id=payload.hotel_id)
    except (ValueError, model_router.EmbeddingError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    await db.commit()
    docs = [schemas.IngestOut(**r.__dict__) for r in results]
    return schemas.SyncPoliciesOut(
        documents=docs, total_chunks=sum(d.chunks_created for d in docs)
    )


@router.post("/knowledge/search", response_model=schemas.SearchOut)
async def search_knowledge(
    payload: schemas.SearchIn, db: AsyncSession = Depends(get_db)
):
    """Retrieval check: ask a question, see which chunks come back and how close.

    This is the pre-chat verification surface - it returns similarity scores
    and the source document for every hit so a wrong answer can be traced to
    the chunk that caused it (NFR-7).
    """
    await _require_hotel(db, payload.hotel_id)
    try:
        hits = await retrieval.search(
            db,
            hotel_id=payload.hotel_id,
            query=payload.query,
            limit=payload.limit,
            min_score=payload.min_score,
        )
    except model_router.EmbeddingError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    return schemas.SearchOut(
        query=payload.query,
        hotel_id=payload.hotel_id,
        embedding=model_router.describe(),
        hits=[schemas.SearchHit(**h.__dict__) for h in hits],
    )


# --- chat (Slice C) -----------------------------------------------------


@router.post("/chat", response_model=schemas.ChatOut)
async def chat(payload: schemas.ChatIn, db: AsyncSession = Depends(get_db)):
    """Send one guest message and get a knowledge-grounded reply.

    Retrieval runs first and a similarity floor is applied before any model
    is called, so an unanswerable question never reaches a generator. When
    `grounded` is false the reply is a fixed refusal and no model ran.
    """
    try:
        turn = await convo.send_message(
            db,
            hotel_id=payload.hotel_id,
            text=payload.message,
            conversation_id=payload.conversation_id,
            channel=payload.channel,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    except (model_router.ChatError, model_router.EmbeddingError) as exc:
        # The turn is not persisted if generation failed; the guest sees a
        # real error rather than a blank bubble.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc

    await db.commit()
    conversation = await convo.get_conversation(
        db, turn.conversation_id, hotel_id=payload.hotel_id
    )
    conversation_status = conversation.status
    return schemas.ChatOut(
        conversation_id=turn.conversation_id,
        reply=turn.reply,
        intent=turn.intent,
        language=turn.language,
        grounded=turn.grounded,
        citations=[schemas.CitationOut(**c.__dict__) for c in turn.citations],
        provider=turn.provider,
        model=turn.model,
        latency_ms=turn.latency_ms,
        top_score=turn.top_score,
        search_text=turn.search_text,
        booking_inquiry_id=turn.booking_inquiry_id,
        conversation_status=conversation_status,
    )


@router.get(
    "/conversations/{conversation_id}", response_model=schemas.ConversationOut
)
async def get_conversation(
    conversation_id: int,
    # NOT scoped_hotel_id: the guest widget polls this for staff replies
    # and has no session. Capability-based - you must know the id.
    hotel_id: int = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Full transcript. hotel_id is required so it doubles as a tenant check."""
    conv = await convo.get_conversation(db, conversation_id, hotel_id=hotel_id)
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation {conversation_id} not found",
        )
    messages = await convo.get_messages(db, conversation_id)
    return schemas.ConversationOut(
        id=conv.id,
        hotel_id=conv.hotel_id,
        channel=conv.channel,
        status=conv.status,
        started_at=conv.started_at,
        resolved_at=conv.resolved_at,
        messages=[schemas.MessageOut.model_validate(m) for m in messages],
    )


@router.post(
    "/conversations/{conversation_id}/request-human",
    response_model=schemas.ConversationOut,
)
async def request_human(
    conversation_id: int,
    payload: schemas.RequestHumanIn,
    db: AsyncSession = Depends(get_db),
):
    """Guest asks for a person. Flips status to escalated (Slice G picks it up)."""
    try:
        conv = await convo.request_human(
            db, conversation_id=conversation_id, hotel_id=payload.hotel_id
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    await db.commit()
    messages = await convo.get_messages(db, conversation_id)
    return schemas.ConversationOut(
        id=conv.id,
        hotel_id=conv.hotel_id,
        channel=conv.channel,
        status=conv.status,
        started_at=conv.started_at,
        resolved_at=conv.resolved_at,
        messages=[schemas.MessageOut.model_validate(m) for m in messages],
    )


# --- booking inquiries (Slice E) ----------------------------------------


# --- staff dashboard (Slice G) ------------------------------------------
#
# Everything below requires X-Staff-Token. These endpoints expose complete
# guest transcripts and the booking pipeline, so they are gated even though
# the guest-facing chat is not. See app/core/auth.py for what that gate is
# and, importantly, what it is not.

async def scoped_hotel_id(
    hotel_id: int = Query(...),
    principal: Principal = Depends(require_staff),
) -> int:
    """`hotel_id` from the query string, checked against the caller's tenant.

    Before staff accounts existed the id was simply trusted, so any holder
    of the shared token could read any property's transcripts. A JWT pins
    the caller to one hotel; a mismatch 404s rather than 403s so the
    dashboard cannot be used to enumerate other properties (NFR-3).
    """
    principal.assert_may_access(hotel_id)
    return hotel_id


staff_router = APIRouter(
    prefix="/receptionist/staff",
    tags=["staff"],
    dependencies=[Depends(require_staff_token)],
)


@staff_router.get("/metrics", response_model=schemas.MetricsOut)
async def staff_metrics(
    hotel_id: int = Depends(scoped_hotel_id), db: AsyncSession = Depends(get_db)
):
    result = await staff_svc.metrics(db, hotel_id=hotel_id)
    return schemas.MetricsOut(**result.__dict__)


@staff_router.get(
    "/conversations", response_model=list[schemas.ConversationSummaryOut]
)
async def staff_list_conversations(
    hotel_id: int = Depends(scoped_hotel_id),
    status_filter: ConversationStatus | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    """Queue view. Escalated first, then most recent."""
    rows = await staff_svc.list_conversations(
        db, hotel_id=hotel_id, status=status_filter
    )
    return [schemas.ConversationSummaryOut(**r.__dict__) for r in rows]


@staff_router.patch(
    "/conversations/{conversation_id}", response_model=schemas.ConversationOut
)
async def staff_set_conversation_status(
    conversation_id: int,
    payload: schemas.ConversationStatusIn,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_staff),
):
    principal.assert_may_access(payload.hotel_id)
    try:
        conversation = await staff_svc.set_conversation_status(
            db,
            conversation_id=conversation_id,
            hotel_id=payload.hotel_id,
            status=payload.status,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    await db.commit()
    messages = await convo.get_messages(db, conversation_id)
    return schemas.ConversationOut(
        id=conversation.id,
        hotel_id=conversation.hotel_id,
        channel=conversation.channel,
        status=conversation.status,
        started_at=conversation.started_at,
        resolved_at=conversation.resolved_at,
        messages=[schemas.MessageOut.model_validate(m) for m in messages],
    )


@staff_router.post(
    "/conversations/{conversation_id}/messages",
    response_model=schemas.ConversationOut,
    status_code=status.HTTP_201_CREATED,
)
async def staff_reply(
    conversation_id: int,
    payload: schemas.StaffMessageIn,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_staff),
):
    """A real person replying in the thread. Does not change the status."""
    principal.assert_may_access(payload.hotel_id)
    try:
        await staff_svc.post_staff_message(
            db,
            conversation_id=conversation_id,
            hotel_id=payload.hotel_id,
            content=payload.content,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    await db.commit()

    conversation = await convo.get_conversation(
        db, conversation_id, hotel_id=payload.hotel_id
    )
    messages = await convo.get_messages(db, conversation_id)
    return schemas.ConversationOut(
        id=conversation.id,
        hotel_id=conversation.hotel_id,
        channel=conversation.channel,
        status=conversation.status,
        started_at=conversation.started_at,
        resolved_at=conversation.resolved_at,
        messages=[schemas.MessageOut.model_validate(m) for m in messages],
    )


@staff_router.get(
    "/booking-inquiries", response_model=list[schemas.BookingInquiryOut]
)
async def staff_list_inquiries(
    hotel_id: int = Depends(scoped_hotel_id),
    status_filter: InquiryStatus | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    """Inquiries collected from guest conversations, newest first (US-8)."""
    return await staff_svc.list_inquiries(
        db, hotel_id=hotel_id, status=status_filter
    )


@staff_router.get("/booking-inquiries.csv", response_class=Response)
async def staff_export_inquiries(
    hotel_id: int = Depends(scoped_hotel_id),
    status_filter: InquiryStatus | None = Query(default=None, alias="status"),
    db: AsyncSession = Depends(get_db),
):
    """Booking inquiries as a spreadsheet (US-8).

    Honours the same status filter as the list endpoint, so "export what I
    am looking at" does what it says rather than silently exporting
    everything.
    """
    inquiries = await staff_svc.list_inquiries(
        db, hotel_id=hotel_id, status=status_filter, limit=5000
    )
    body = export.to_csv(inquiries)

    return Response(
        # utf-8-sig, not utf-8: without the BOM, Excel on Windows renders
        # Devanagari in the guest's own words as mojibake, and Windows
        # Excel is exactly where these files are opened.
        content=body.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="{export.filename(hotel_id)}"',
            # The browser fetch reads this to name the download; without it
            # a cross-origin response hides every header but the safelist.
            "Access-Control-Expose-Headers": "Content-Disposition",
        },
    )


@staff_router.patch(
    "/booking-inquiries/{inquiry_id}", response_model=schemas.BookingInquiryOut
)
async def staff_set_inquiry_status(
    inquiry_id: int,
    payload: schemas.InquiryStatusIn,
    db: AsyncSession = Depends(get_db),
    principal: Principal = Depends(require_staff),
):
    principal.assert_may_access(payload.hotel_id)
    try:
        inquiry = await staff_svc.set_inquiry_status(
            db,
            inquiry_id=inquiry_id,
            hotel_id=payload.hotel_id,
            status=payload.status,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    await db.commit()
    return inquiry


@staff_router.get("/knowledge/health", response_model=schemas.KnowledgeHealthOut)
async def knowledge_health(
    hotel_id: int = Depends(scoped_hotel_id), db: AsyncSession = Depends(get_db)
):
    """Flag knowledge the assistant cannot reliably answer from.

    Checks the policies staff typed in setup as well as ingested documents,
    because the measured hallucination came from a policy field containing
    "10/12" - the entry point, not the knowledge base.
    """
    from app.platform.models import HotelPolicy

    issues: list[schemas.KnowledgeIssue] = []

    policies = (
        await db.execute(
            select(HotelPolicy).where(HotelPolicy.hotel_id == hotel_id)
        )
    ).scalars().all()
    for policy in policies:
        label = policy.category.value.replace("_", " ").title() + " policy"
        for w in quality.assess(policy.content_text, label=f"The {label}"):
            issues.append(
                schemas.KnowledgeIssue(
                    source="policy", source_id=policy.id, title=label,
                    severity=w.severity, code=w.code, message=w.message,
                    excerpt=(policy.content_text or "")[:120],
                )
            )

    documents = (
        await db.execute(
            select(KnowledgeDocument).where(KnowledgeDocument.hotel_id == hotel_id)
        )
    ).scalars().all()
    for document in documents:
        for w in quality.assess(document.raw_content, label=f"{document.title!r}"):
            issues.append(
                schemas.KnowledgeIssue(
                    source="document", source_id=document.id,
                    title=document.title, severity=w.severity, code=w.code,
                    message=w.message,
                    excerpt=(document.raw_content or "")[:120],
                )
            )

    issues.sort(key=lambda i: 0 if i.severity == "high" else 1)
    return schemas.KnowledgeHealthOut(
        hotel_id=hotel_id,
        documents_checked=len(documents),
        policies_checked=len(policies),
        issues=issues,
    )


@staff_router.get("/degradation", response_model=schemas.DegradationOut)
async def staff_degradation(
    hotel_id: int = Depends(scoped_hotel_id),
    minutes: int = Query(default=15, ge=1, le=1440),
    db: AsyncSession = Depends(get_db),
):
    """Recent turns that fell back to rules because a model call failed.

    Deliberately keyless operation is not degradation and is not counted.
    """
    result = await staff_svc.degradation(db, hotel_id=hotel_id, minutes=minutes)
    return schemas.DegradationOut(**result.__dict__)
