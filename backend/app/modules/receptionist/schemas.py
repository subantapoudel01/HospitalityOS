"""Request/response schemas for the receptionist module."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.receptionist.services.conversation import ChatIntent
from app.modules.receptionist.models import (
    Channel,
    ConversationStatus,
    InquiryStatus,
    KnowledgeSourceType,
    Sender,
)


class DocumentIn(BaseModel):
    hotel_id: int
    title: str = Field(min_length=1, max_length=200)
    raw_content: str = Field(min_length=1)
    source_type: KnowledgeSourceType = KnowledgeSourceType.upload


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hotel_id: int
    title: str
    source_type: KnowledgeSourceType
    created_at: datetime
    chunk_count: int


class IngestOut(BaseModel):
    document_id: int
    title: str
    chunks_created: int
    # Advisory: content the assistant may not be able to answer from.
    warnings: list[dict] = Field(default_factory=list)


class SyncPoliciesIn(BaseModel):
    hotel_id: int


class SyncPoliciesOut(BaseModel):
    documents: list[IngestOut]
    total_chunks: int


class SearchIn(BaseModel):
    hotel_id: int
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    min_score: float | None = Field(default=None, ge=-1.0, le=1.0)


class SearchHit(BaseModel):
    chunk_id: int
    document_id: int
    document_title: str
    source_type: str
    chunk_text: str
    score: float
    token_count: int
    # The two halves behind `score`. Exposed because this endpoint exists
    # to explain a wrong answer, and "the vector missed it and lexical
    # carried it" is not recoverable from the combined number alone.
    vector_score: float = 0.0
    lexical_score: float = 0.0


class SearchOut(BaseModel):
    query: str
    hotel_id: int
    embedding: dict
    hits: list[SearchHit]


# --- chat (Slice C) -----------------------------------------------------


class ChatIn(BaseModel):
    hotel_id: int
    message: str = Field(min_length=1, max_length=4000)
    # Omit to start a new conversation; pass it back to continue one.
    conversation_id: int | None = None
    channel: Channel = Channel.website


class CitationOut(BaseModel):
    chunk_id: int
    document_id: int
    document_title: str
    score: float


class ChatOut(BaseModel):
    conversation_id: int
    reply: str
    # What kind of turn this was. `grounded` is kept for compatibility and
    # is true only for intent == "answer".
    intent: ChatIntent
    # Detected language of the guest message: en | ne_romanized |
    # ne_devanagari.
    language: str
    grounded: bool
    citations: list[CitationOut]
    provider: str
    model: str
    latency_ms: int
    top_score: float | None
    # The English text actually searched, when the question was translated.
    # Surfaced so a bad answer can be traced to a bad translation.
    search_text: str | None = None
    # Set when a booking request had every required slot and was saved.
    booking_inquiry_id: int | None = None
    # So the widget can reflect an escalation triggered from chat rather
    # than only from the "Talk to a person" button.
    conversation_status: ConversationStatus


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sender: Sender
    content: str
    language_detected: str | None
    created_at: datetime


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hotel_id: int
    channel: Channel
    status: ConversationStatus
    started_at: datetime
    resolved_at: datetime | None
    messages: list[MessageOut]


class RequestHumanIn(BaseModel):
    hotel_id: int


# --- booking inquiries (Slice E) ----------------------------------------


class BookingInquiryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conversation_id: int
    hotel_id: int
    check_in_date: date
    check_out_date: date
    guest_count: int
    room_type_preference: str | None
    status: InquiryStatus
    raw_request: str | None
    created_at: datetime


# --- staff dashboard (Slice G) ------------------------------------------


class MetricsOut(BaseModel):
    conversations_total: int
    escalated: int
    resolved: int
    inquiries_new: int


class ConversationSummaryOut(BaseModel):
    id: int
    status: ConversationStatus
    channel: str
    started_at: datetime
    resolved_at: datetime | None
    message_count: int
    last_message_at: datetime | None
    last_message_preview: str | None
    # Escalated with the guest speaking last: nobody has replied yet.
    awaiting_staff: bool
    # Why it was escalated (US-4). None means the guest asked outright.
    escalation_trigger: str | None = None
    escalation_reason: str | None = None


class ConversationStatusIn(BaseModel):
    hotel_id: int
    status: ConversationStatus


class StaffMessageIn(BaseModel):
    hotel_id: int
    content: str = Field(min_length=1, max_length=4000)


class InquiryStatusIn(BaseModel):
    hotel_id: int
    status: InquiryStatus


class KnowledgeIssue(BaseModel):
    source: str  # policy | document
    source_id: int
    title: str
    severity: str
    code: str
    message: str
    excerpt: str


class KnowledgeHealthOut(BaseModel):
    hotel_id: int
    documents_checked: int
    policies_checked: int
    issues: list[KnowledgeIssue]


class DegradationOut(BaseModel):
    """Whether recent turns ran on rules because a model call failed."""

    degraded: bool
    window_minutes: int
    events: int
    last_at: datetime | None
    providers: list[str]
    by_purpose: dict[str, int]
