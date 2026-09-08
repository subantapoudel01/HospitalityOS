"""SQLAlchemy models for the receptionist module.

The knowledge models moved to conversa.rag.models during the engine
extraction - a document and a chunk are domain-agnostic. Import them
from there, not from here.
"""

from app.modules.receptionist.models.booking import (
    BookingInquiry,
    InquiryStatus,
)
from app.modules.receptionist.models.conversation import (
    AiPurpose,
    AiRequest,
    Channel,
    Conversation,
    ConversationStatus,
    Message,
    Sender,
)

__all__ = [
    "AiPurpose",
    "BookingInquiry",
    "InquiryStatus",
    "AiRequest",
    "Channel",
    "Conversation",
    "ConversationStatus",
    "Message",
    "Sender",
]
