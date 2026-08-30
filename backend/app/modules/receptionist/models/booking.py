"""
Booking inquiries: the structured record extracted from a guest conversation.

An inquiry is a lead, not a reservation. The MVP has no inventory locking
and no payment, so this records what the guest asked for and leaves staff to
follow up (US-8). `status` tracks that follow-up, not the stay.
"""
from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class InquiryStatus(str, enum.Enum):
    new = "new"
    contacted = "contacted"
    confirmed = "confirmed"
    lost = "lost"


class BookingInquiry(Base):
    __tablename__ = "booking_inquiries"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    hotel_id: Mapped[int] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    guest_id: Mapped[int | None] = mapped_column(
        ForeignKey("guests.id", ondelete="SET NULL"), nullable=True
    )

    check_in_date: Mapped[date] = mapped_column(Date, nullable=False)
    check_out_date: Mapped[date] = mapped_column(Date, nullable=False)
    guest_count: Mapped[int] = mapped_column(Integer, nullable=False)
    room_type_preference: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )

    status: Mapped[InquiryStatus] = mapped_column(
        Enum(InquiryStatus, name="inquiry_status",
             values_callable=lambda e: [m.value for m in e]),
        nullable=False, default=InquiryStatus.new,
    )
    # What the guest actually typed. Extraction is a model output and can be
    # subtly wrong; staff need the original wording to sanity-check dates
    # before they call anyone back.
    raw_request: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    conversation = relationship("Conversation")

    @property
    def nights(self) -> int:
        return (self.check_out_date - self.check_in_date).days
