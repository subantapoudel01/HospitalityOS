"""
Platform data models — shared by every HospitalityOS module.

Slice A scope: the hotel profile and the manual room/rate/policy data a
staff member enters during setup (US-5). Every table carries hotel_id so
tenant scoping (NFR-3) is enforceable on the row.
"""
from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class PolicyCategory(str, enum.Enum):
    """Fixed set from DATABASE_DESIGN.md — staff pick one, they don't invent them."""

    checkin_checkout = "checkin_checkout"
    cancellation = "cancellation"
    pets = "pets"
    payment = "payment"
    other = "other"


class Hotel(Base):
    __tablename__ = "hotels"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # `description` is not in the original DATABASE_DESIGN.md table list;
    # added for Slice A because staff need somewhere to describe the
    # property, and it doubles as RAG source material later.
    description: Mapped[str | None] = mapped_column(Text)
    city: Mapped[str | None] = mapped_column(String(120))
    address: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(40))
    whatsapp_number: Mapped[str | None] = mapped_column(String(40))
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="NPR", server_default="NPR"
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Asia/Kathmandu",
        server_default="Asia/Kathmandu",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    room_types: Mapped[list["RoomType"]] = relationship(
        back_populates="hotel",
        cascade="all, delete-orphan",
        order_by="RoomType.id",
    )
    policies: Mapped[list["HotelPolicy"]] = relationship(
        back_populates="hotel",
        cascade="all, delete-orphan",
        order_by="HotelPolicy.id",
    )


class RoomType(Base):
    __tablename__ = "room_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    hotel_id: Mapped[int] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    # Numeric, not float — these are prices. NPR per night.
    base_rate: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    max_occupancy: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    # Python attribute is `amenities`; the DB column keeps the name the
    # design doc specifies (`amenities_json`).
    amenities: Mapped[list[str]] = mapped_column(
        "amenities_json", JSON, nullable=False, default=list
    )

    hotel: Mapped["Hotel"] = relationship(back_populates="room_types")


class HotelPolicy(Base):
    __tablename__ = "hotel_policies"

    id: Mapped[int] = mapped_column(primary_key=True)
    hotel_id: Mapped[int] = mapped_column(
        ForeignKey("hotels.id", ondelete="CASCADE"), nullable=False, index=True
    )
    category: Mapped[PolicyCategory] = mapped_column(
        Enum(
            PolicyCategory,
            name="policy_category",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    content_text: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    hotel: Mapped["Hotel"] = relationship(back_populates="policies")
