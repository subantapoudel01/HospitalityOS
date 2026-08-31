"""
The conversation loop: small talk, language, retrieve, ground, generate.

Order matters and is deliberate:

  0. Small talk short-circuits first. A greeting is not a question, and
     running it through retrieval produced the strict refusal that made the
     widget feel broken. Costs nothing: no database hit, no model call.
  1. Language is detected before anything is searched, because the query
     may need translating to match an English knowledge base.
  2. Nepali questions are translated to English for retrieval. Slice B
     measured why: a native-Devanagari question scored 0.17 against the
     English corpus and its answer was not even in the top three.
  3. The similarity floor is applied BEFORE any generation, so a question
     the knowledge base cannot answer never reaches a generator.

That last point is what turns the PRD's "zero hallucination on hotel-
specific facts" from a prompt instruction into a structural property - with
the caveat recorded in config.py that the floor alone cannot separate every
unanswerable question, and the grounding prompt is the second line.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import model_router
from app.core.config import settings
from app.modules.receptionist.models import (
    AiPurpose,
    AiRequest,
    Channel,
    Conversation,
    ConversationStatus,
    Message,
    Sender,
)
from app.modules.receptionist.models import BookingInquiry
from app.modules.receptionist.rag import retrieval
from app.modules.receptionist.services import (
    booking,
    frustration,
    intent as intent_svc,
    replies,
)
from app.modules.receptionist.services.intent import GuestIntent
from app.modules.receptionist.services.language import (
    Detection,
    Language,
    detect,
    reply_instruction,
)
from app.platform.models import Hotel


class ChatIntent(str, enum.Enum):
    """What kind of turn this was.

    Separate from `grounded` because a greeting is neither an answer from
    the knowledge base nor a refusal, and overloading a boolean to mean
    three things is how confusing bugs start.
    """

    smalltalk = "smalltalk"
    answer = "answer"
    refusal = "refusal"
    booking = "booking"
    escalation = "escalation"
    # Guest wrote again after a handoff; recorded for staff, not answered.
    stood_down = "stood_down"


# Guest-facing fixed text lives in replies.py so intent.py can share it
# without a circular import. Re-exported here because callers and tests
# already reach for convo.REFUSALS.
REFUSALS = replies.REFUSALS
ESCALATION_CONFIRMED = replies.ESCALATION_CONFIRMED
STAND_DOWN = replies.STAND_DOWN

# Prepended when the guest wrote Nepali but no model was available to reply
# in Nepali (extractive mode). They still get the real information, plus a
# clear route to a human who speaks their language.
DEGRADED_NOTE: dict[Language, str] = {
    Language.ne_romanized: (
        "[Nepali ma jawaf dina milena. Yo jankari English ma cha. Nepali ma "
        "kura garna staff lai bolauna sakinchha.]"
    ),
    Language.ne_devanagari: (
        "[अहिले नेपालीमा जवाफ दिन मिलेन। तलको जानकारी अंग्रेजीमा छ। "
        "नेपालीमा कुरा गर्न स्टाफलाई बोलाउन सकिन्छ।]"
    ),
}

# Recorded in ai_requests.model_used when no model was involved, so these
# turns stay queryable next to real calls. Neither is a model name.
NO_MODEL = "deterministic-refusal"
SMALLTALK_MODEL = "smalltalk-rules"
ESCALATION_MODEL = "escalation-rules"
# Recorded in model_used when a turn ran on rules because a model call
# failed. Paired with degraded_from, which names the provider.
RULES_FALLBACK = "rules-fallback"

SYSTEM_PROMPT = """You are the AI receptionist for {hotel_name}.

Answer using ONLY the CONTEXT below. The context is this hotel's own
knowledge base.

Rules:
- If the context does not contain the answer, reply with EXACTLY this
  sentence and nothing else:
    {refusal_text}
  Do not guess, and do not reword it. The exact wording matters: the
  system recognises this sentence, and uses it to understand "yes" as
  "yes, fetch someone" and to notice when a guest has hit several dead
  ends in a row. A paraphrase reads the same to the guest and is
  invisible to both.
- Never invent prices, times, room names, or policies. Never fall back on
  general knowledge about hotels.
- Do not INTERPRET context you cannot read with confidence. If the only
  relevant entry is a bare code, a fragment, or a notation whose meaning is
  not spelled out, quote what the record actually says and offer to have a
  staff member confirm it. Do not convert it into a time, a price or a date.
  A record reading "10/12" might be hours, dates or a month and day - say
  the record shows 10/12 and that you cannot confirm which, rather than
  choosing one. Guessing correctly some of the time is still guessing.
- Do not mention "the context" or "the knowledge base" to the guest. Just
  answer as a receptionist would.
- {reply_instruction}
- Keep it to two or three sentences at most.
- Amounts are in {currency} unless the context says otherwise.

CONTEXT:
{context}"""


@dataclass
class Citation:
    chunk_id: int
    document_id: int
    document_title: str
    score: float


@dataclass
class ChatTurn:
    conversation_id: int
    reply: str
    grounded: bool
    intent: ChatIntent
    language: str
    citations: list[Citation] = field(default_factory=list)
    provider: str = ""
    model: str = ""
    latency_ms: int = 0
    top_score: float | None = None
    # The English text actually searched, when the question was translated.
    search_text: str | None = None
    # Set once a booking request has every required slot and was saved.
    booking_inquiry_id: int | None = None


async def _load_hotel(db: AsyncSession, hotel_id: int) -> Hotel | None:
    return (
        await db.execute(select(Hotel).where(Hotel.id == hotel_id))
    ).scalar_one_or_none()


async def get_conversation(
    db: AsyncSession, conversation_id: int, *, hotel_id: int | None = None
) -> Conversation | None:
    """Fetch a conversation, optionally asserting it belongs to a hotel.

    The hotel_id check is the tenant boundary: without it a caller could
    continue somebody else's conversation by guessing an integer.
    """
    stmt = select(Conversation).where(Conversation.id == conversation_id)
    if hotel_id is not None:
        stmt = stmt.where(Conversation.hotel_id == hotel_id)
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_messages(db: AsyncSession, conversation_id: int) -> list[Message]:
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.id)
    )
    return list(result.scalars().all())


def _build_context_block(hits: list[retrieval.RetrievedChunk]) -> str:
    return "\n\n".join(
        f"[{i}] ({h.document_title}) {h.chunk_text}" for i, h in enumerate(hits, 1)
    )


def _log_ai_request(
    db: AsyncSession,
    *,
    conversation_id: int,
    model_used: str,
    purpose: AiPurpose,
    result: model_router.ChatResult | None = None,
    chunk_ids: list[int] | None = None,
    degraded_from: str | None = None,
) -> None:
    completion = None
    if result is not None and result.completion_tokens is not None:
        # Thinking tokens bill at the output rate, so fold them in rather
        # than under-reporting what the turn actually cost.
        completion = result.completion_tokens + (result.thinking_tokens or 0)
    db.add(
        AiRequest(
            conversation_id=conversation_id,
            model_used=model_used,
            purpose=purpose,
            prompt_tokens=result.prompt_tokens if result else None,
            completion_tokens=completion,
            latency_ms=result.latency_ms if result else 0,
            cost_estimate=result.cost_estimate if result else 0.0,
            retrieved_chunk_ids=chunk_ids,
            # Explicit argument wins; otherwise take what the router already
            # worked out. ChatResult.degraded_from has existed since Slice C
            # and was being computed and thrown away.
            degraded_from=degraded_from
            or (result.degraded_from if result else None),
        )
    )


async def _resolve_language(
    db: AsyncSession, *, conversation_id: int, text: str
) -> tuple[Language, Detection]:
    """Deterministic first; only pay for a model when genuinely unsure."""
    detection = detect(text)
    if detection.language is not None:
        return detection.language, detection

    if not settings.chat_language_model_fallback:
        return Language.en, detection

    try:
        result = await run_in_threadpool(model_router.classify_language, text)
    except model_router.ChatError:
        # Classification is a nicety, not a hard requirement. Defaulting to
        # English keeps the turn alive rather than failing the guest - but a
        # Nepali speaker just got treated as an English one, so record it.
        #
        # Only when a provider actually exists. Without one, _fast_call
        # raises by design and calling that "degradation" would light the
        # dashboard banner permanently on every keyless deployment.
        if model_router.fast_available():
            _log_ai_request(
                db,
                conversation_id=conversation_id,
                model_used=RULES_FALLBACK,
                purpose=AiPurpose.classification,
                degraded_from=model_router.FAST_PROVIDER,
            )
        return Language.en, detection

    _log_ai_request(
        db,
        conversation_id=conversation_id,
        model_used=result.model,
        purpose=AiPurpose.classification,
        result=result,
    )
    try:
        language = Language(result.text)
    except ValueError:
        language = Language.en
    return language, Detection(language, "model", 0.9)


async def send_message(
    db: AsyncSession,
    *,
    hotel_id: int,
    text: str,
    conversation_id: int | None = None,
    channel: Channel = Channel.website,
) -> ChatTurn:
    """Handle one guest turn end to end. Caller commits."""
    hotel = await _load_hotel(db, hotel_id)
    if hotel is None:
        raise LookupError(f"Hotel {hotel_id} not found")

    if conversation_id is None:
        conversation = Conversation(hotel_id=hotel_id, channel=channel)
        db.add(conversation)
        await db.flush()
    else:
        conversation = await get_conversation(db, conversation_id, hotel_id=hotel_id)
        if conversation is None:
            raise LookupError(f"Conversation {conversation_id} not found")

    # --- 0. language, deterministic where possible -----------------------
    language, _detection = await _resolve_language(
        db, conversation_id=conversation.id, text=text
    )

    db.add(
        Message(
            conversation_id=conversation.id, sender=Sender.guest,
            content=text, language_detected=language.value,
        )
    )
    await db.flush()

    prior = [
        {
            "role": "assistant" if m.sender == Sender.ai else "user",
            "content": m.content,
        }
        for m in await get_messages(db, conversation.id)
        if m.sender in (Sender.guest, Sender.ai)
    ][:-1]

    # --- 0b. already escalated: the AI stands down -----------------------
    # A human has been notified and can see this thread. Answering anyway
    # risks contradicting the staff member who is about to reply, which is
    # the main way a handoff goes wrong. Record and acknowledge instead.
    if conversation.status is ConversationStatus.escalated:
        reply_text = STAND_DOWN.get(language, STAND_DOWN[Language.en])
        db.add(
            Message(
                conversation_id=conversation.id, sender=Sender.ai,
                content=reply_text, language_detected=language.value,
            )
        )
        await db.flush()
        return ChatTurn(
            conversation_id=conversation.id,
            reply=reply_text,
            grounded=False,
            intent=ChatIntent.stood_down,
            language=language.value,
            provider=ESCALATION_MODEL,
            model=ESCALATION_MODEL,
        )

    # --- 0c. the guest has told us this is not working (US-4) ------------
    # Checked before classification, so it costs nothing and still works
    # when the provider is rate-limited - which is exactly when guests
    # start getting refusals and saying so.
    if settings.chat_auto_escalate:
        signal = frustration.detect_frustration(text)
        if signal is not None:
            return await _escalate(
                db, conversation=conversation, language=language,
                signal=signal,
            )

    # --- 1. intent, BEFORE any retrieval ---------------------------------
    decision = await run_in_threadpool(
        lambda: intent_svc.classify(
            text, hotel_name=hotel.name, language=language, history=prior
        )
    )
    if decision.telemetry is not None:
        _log_ai_request(
            db,
            conversation_id=conversation.id,
            model_used=decision.telemetry.model,
            purpose=AiPurpose.classification,
            result=decision.telemetry,
        )
    elif decision.degraded_from:
        # Previously this path wrote nothing at all, which is why a whole
        # afternoon of rules-routed turns left no trace anywhere.
        _log_ai_request(
            db,
            conversation_id=conversation.id,
            model_used=RULES_FALLBACK,
            purpose=AiPurpose.classification,
            degraded_from=decision.degraded_from,
        )

    if decision.intent is GuestIntent.escalation:
        return await _escalate(
            db, conversation=conversation, language=language,
            telemetry=decision.telemetry,
        )

    if decision.intent is GuestIntent.smalltalk and decision.reply:
        # The classifier already wrote the reply. No vector search happens
        # for a greeting, which is the entire point of doing this first.
        db.add(
            Message(
                conversation_id=conversation.id, sender=Sender.ai,
                content=decision.reply, language_detected=language.value,
            )
        )
        if decision.method == "rules":
            _log_ai_request(
                db,
                conversation_id=conversation.id,
                model_used=SMALLTALK_MODEL,
                purpose=AiPurpose.chat,
            )
        await db.flush()
        return ChatTurn(
            conversation_id=conversation.id,
            reply=decision.reply,
            grounded=False,
            intent=ChatIntent.smalltalk,
            language=language.value,
            provider=decision.method,
            model=(
                SMALLTALK_MODEL
                if decision.method == "rules"
                else (decision.telemetry.model if decision.telemetry else "")
            ),
            latency_ms=decision.telemetry.latency_ms if decision.telemetry else 0,
        )

    if decision.intent is GuestIntent.booking_request:
        return await _handle_booking(
            db, hotel=hotel, conversation=conversation, text=text,
            language=language,
        )

    # --- 2. translate for retrieval --------------------------------------
    search_text = text
    translated = None
    if language.is_nepali and settings.chat_translate_queries:
        try:
            tr = await run_in_threadpool(model_router.translate, text)
            translated = tr.text
            search_text = tr.text
            _log_ai_request(
                db,
                conversation_id=conversation.id,
                model_used=tr.model,
                purpose=AiPurpose.translation,
                result=tr,
            )
        except model_router.ChatError:
            # No translator available. Search the original text: Slice B
            # showed cross-lingual retrieval works for loanwords even when
            # it fails on native vocabulary, so this is degraded, not dead.
            # Slice B also measured how much worse: a native Devanagari
            # question scored 0.17 untranslated against 0.52 translated.
            search_text = text
            if model_router.fast_available():
                _log_ai_request(
                    db,
                    conversation_id=conversation.id,
                    model_used=RULES_FALLBACK,
                    purpose=AiPurpose.translation,
                    degraded_from=model_router.FAST_PROVIDER,
                )

    # --- 3. retrieve, scoped to this hotel -------------------------------
    hits = await retrieval.search(
        db, hotel_id=hotel_id, query=search_text, limit=settings.chat_top_k
    )
    top_score = hits[0].score if hits else None

    # --- 4. floor, BEFORE any generation ---------------------------------
    relevant = [h for h in hits if h.score >= settings.chat_min_score]

    if not relevant:
        # --- 4a. going nowhere: hand over INSTEAD of refusing again ------
        # Decided before the refusal is written, so the guest gets one
        # message rather than "I don't have that information" followed
        # immediately by "I've asked a staff member" - saying both is the
        # exact clumsiness this is meant to remove.
        #
        # The guest may be perfectly polite throughout. A run of refusals
        # usually means the knowledge base is missing something, and
        # nobody finds out unless a person is pulled in.
        if settings.chat_auto_escalate:
            signal = frustration.detect_dead_end(
                await get_messages(db, conversation.id),
                refusal_texts=frustration.refusal_text_set(REFUSALS),
                threshold=settings.chat_dead_end_turns,
                pending_refusal=True,
            )
            if signal is not None:
                _log_ai_request(
                    db,
                    conversation_id=conversation.id,
                    model_used=NO_MODEL,
                    purpose=AiPurpose.chat,
                    chunk_ids=[h.chunk_id for h in hits],
                )
                return await _escalate(
                    db, conversation=conversation, language=language,
                    signal=signal,
                )

        reply_text = REFUSALS.get(language, REFUSALS[Language.en])
        db.add(
            Message(
                conversation_id=conversation.id, sender=Sender.ai,
                content=reply_text, language_detected=language.value,
            )
        )

        _log_ai_request(
            db,
            conversation_id=conversation.id,
            model_used=NO_MODEL,
            purpose=AiPurpose.chat,
            # Keep the rejected candidates: knowing what nearly matched is
            # how the floor gets tuned later.
            chunk_ids=[h.chunk_id for h in hits],
        )
        await db.flush()
        return ChatTurn(
            conversation_id=conversation.id,
            reply=reply_text,
            grounded=False,
            intent=ChatIntent.refusal,
            language=language.value,
            provider=NO_MODEL,
            model=NO_MODEL,
            top_score=top_score,
            search_text=translated,
        )

    # --- 5. generate, grounded in the retrieved passages -----------------
    system = SYSTEM_PROMPT.format(
        hotel_name=hotel.name,
        currency=hotel.currency or "NPR",
        reply_instruction=reply_instruction(language),
        # In the guest's own language, so a Nepali refusal is recognisable
        # too rather than only the English one.
        refusal_text=REFUSALS.get(language, REFUSALS[Language.en]),
        context=_build_context_block(relevant),
    )
    history = await get_messages(db, conversation.id)
    turns = [
        {
            "role": "assistant" if m.sender == Sender.ai else "user",
            "content": m.content,
        }
        for m in history
        if m.sender in (Sender.guest, Sender.ai)
    ][-settings.chat_history_turns :]

    result = await run_in_threadpool(
        lambda: model_router.chat(
            system=system,
            messages=turns,
            context=[h.chunk_text for h in relevant],
        )
    )

    reply_text = result.text
    if language.is_nepali and result.provider == "extractive":
        # Extractive cannot write Nepali; it returns the English passages
        # verbatim. Say so in the guest's language instead of silently
        # handing a Nepali speaker a wall of English.
        note = DEGRADED_NOTE.get(language)
        if note:
            reply_text = note + "\n\n" + reply_text

    db.add(
        Message(
            conversation_id=conversation.id, sender=Sender.ai,
            content=reply_text, language_detected=language.value,
        )
    )
    _log_ai_request(
        db,
        conversation_id=conversation.id,
        model_used=result.model,
        purpose=AiPurpose.chat,
        result=result,
        chunk_ids=[h.chunk_id for h in relevant],
    )
    await db.flush()

    # --- 5b. the model declined -----------------------------------------
    # Retrieval cleared the floor but the passages did not actually answer,
    # so the prompt had the model reply with the fixed refusal. To the
    # guest that is a dead end exactly like the deterministic one above,
    # and it is by far the more common of the two once a hosted provider
    # is generating: the floor only catches questions that retrieve
    # nothing at all.
    declined = replies.is_fixed_refusal(reply_text)
    if declined and settings.chat_auto_escalate:
        signal = frustration.detect_dead_end(
            await get_messages(db, conversation.id),
            refusal_texts=frustration.refusal_text_set(REFUSALS),
            threshold=settings.chat_dead_end_turns,
        )
        if signal is not None:
            return await _escalate(
                db, conversation=conversation, language=language,
                signal=signal,
            )

    return ChatTurn(
        conversation_id=conversation.id,
        reply=reply_text,
        grounded=True,
        # Report what actually happened. Calling a decline an "answer"
        # is what made the golden-set grader mis-score refusals, and it
        # would hide the same thing from the dashboard.
        intent=ChatIntent.refusal if declined else ChatIntent.answer,
        language=language.value,
        citations=[
            Citation(
                chunk_id=h.chunk_id,
                document_id=h.document_id,
                document_title=h.document_title,
                score=h.score,
            )
            for h in relevant
        ],
        provider=result.provider,
        model=result.model,
        latency_ms=result.latency_ms,
        top_score=top_score,
        search_text=translated,
    )


async def _escalate(
    db: AsyncSession,
    *,
    conversation: Conversation,
    language: Language,
    telemetry: model_router.ChatResult | None = None,
    signal: frustration.Signal | None = None,
) -> ChatTurn:
    """Flag for staff and confirm - the same effect as the widget button.

    The confirmation is fixed text emitted only AFTER the status actually
    changes. Letting a model word this is how the guest gets told "I will
    forward your request" while nothing is forwarded.

    `signal` is set when the AI escalated on its own (US-4). It is stored
    so staff see WHY before opening the transcript - "the guest said this
    was not helping" and "the guest asked for a person" need different
    opening lines. None means the guest asked.
    """
    conversation.status = ConversationStatus.escalated
    conversation.resolved_at = None
    if signal is not None:
        conversation.escalation_trigger = signal.trigger
        conversation.escalation_reason = signal.reason

    reply_text = ESCALATION_CONFIRMED.get(
        language, ESCALATION_CONFIRMED[Language.en]
    )
    db.add(
        Message(
            conversation_id=conversation.id, sender=Sender.ai,
            content=reply_text, language_detected=language.value,
        )
    )
    _log_ai_request(
        db,
        conversation_id=conversation.id,
        model_used=telemetry.model if telemetry else ESCALATION_MODEL,
        purpose=AiPurpose.chat,
        result=telemetry,
    )
    await db.flush()
    return ChatTurn(
        conversation_id=conversation.id,
        reply=reply_text,
        grounded=False,
        intent=ChatIntent.escalation,
        language=language.value,
        provider=ESCALATION_MODEL,
        model=telemetry.model if telemetry else ESCALATION_MODEL,
        latency_ms=telemetry.latency_ms if telemetry else 0,
    )


def _today_for(hotel: Hotel):
    """Today in the hotel's own timezone, not the server's.

    A guest in Pokhara saying "tomorrow" means tomorrow there. Resolving
    that against UTC puts every late-evening request on the wrong day.
    """
    try:
        tz = ZoneInfo(hotel.timezone or "Asia/Kathmandu")
    except (ZoneInfoNotFoundError, ValueError, KeyError):
        # No tzdata on this host (bare Windows, or a slim image without the
        # tzdata package). stdlib UTC always exists, unlike ZoneInfo("UTC"),
        # which needs the same database that just failed to load.
        tz = timezone.utc
    return datetime.now(tz).date()


async def _handle_booking(
    db: AsyncSession,
    *,
    hotel: Hotel,
    conversation: Conversation,
    text: str,
    language: Language,
) -> ChatTurn:
    """Collect booking details across turns, then save the inquiry.

    Slots are re-extracted from the whole transcript each turn rather than
    accumulated, so a guest changing their mind corrects the record instead
    of layering onto a stale draft.
    """
    messages = await get_messages(db, conversation.id)
    transcript = "\n".join(
        ("Guest: " if m.sender == Sender.guest else "Assistant: ") + m.content
        for m in messages
        if m.sender in (Sender.guest, Sender.ai)
    )

    extraction = await run_in_threadpool(
        lambda: booking.extract(
            transcript,
            today=_today_for(hotel),
            timezone=hotel.timezone or "Asia/Kathmandu",
            language=language,
        )
    )
    if extraction.telemetry is not None:
        _log_ai_request(
            db,
            conversation_id=conversation.id,
            model_used=extraction.telemetry.model,
            purpose=AiPurpose.classification,
            result=extraction.telemetry,
        )
    elif extraction.degraded_from:
        _log_ai_request(
            db,
            conversation_id=conversation.id,
            model_used=RULES_FALLBACK,
            purpose=AiPurpose.classification,
            degraded_from=extraction.degraded_from,
        )

    inquiry_id = None
    if extraction.slots.complete:
        inquiry = BookingInquiry(
            conversation_id=conversation.id,
            hotel_id=hotel.id,
            check_in_date=extraction.slots.check_in_date,
            check_out_date=extraction.slots.check_out_date,
            guest_count=extraction.slots.guest_count,
            room_type_preference=extraction.slots.room_type_preference,
            raw_request=text,
        )
        db.add(inquiry)
        await db.flush()
        inquiry_id = inquiry.id
        reply_text = booking.confirmation_text(extraction.slots, language=language)
    else:
        reply_text = extraction.follow_up or booking._fallback_follow_up(
            extraction.slots.missing
        )

    db.add(
        Message(
            conversation_id=conversation.id, sender=Sender.ai,
            content=reply_text, language_detected=language.value,
        )
    )
    await db.flush()

    return ChatTurn(
        conversation_id=conversation.id,
        reply=reply_text,
        grounded=False,
        intent=ChatIntent.booking,
        language=language.value,
        provider="booking",
        model=extraction.telemetry.model if extraction.telemetry else "booking-rules",
        latency_ms=extraction.telemetry.latency_ms if extraction.telemetry else 0,
        booking_inquiry_id=inquiry_id,
    )


async def request_human(
    db: AsyncSession, *, conversation_id: int, hotel_id: int
) -> Conversation:
    """Flag a conversation for staff takeover.

    Slice D only flips the flag and acknowledges. The live staff dashboard
    that reacts to it is Slice G; this is the seam it will attach to.
    """
    conversation = await get_conversation(db, conversation_id, hotel_id=hotel_id)
    if conversation is None:
        raise LookupError(f"Conversation {conversation_id} not found")

    conversation.status = ConversationStatus.escalated
    conversation.resolved_at = None

    # Answer in whatever language the guest has been using. Before this the
    # button always replied in English, which quietly undid Slice D for the
    # one message where being understood matters most.
    language = Language.en
    for message in reversed(await get_messages(db, conversation.id)):
        if message.sender is Sender.guest and message.language_detected:
            try:
                language = Language(message.language_detected)
            except ValueError:
                language = Language.en
            break

    db.add(
        Message(
            conversation_id=conversation.id,
            sender=Sender.ai,
            content=ESCALATION_CONFIRMED.get(
                language, ESCALATION_CONFIRMED[Language.en]
            ),
            language_detected=language.value,
        )
    )
    await db.flush()
    return conversation
