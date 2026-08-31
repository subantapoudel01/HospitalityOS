"""
Pydantic request/response schemas for the platform API.

The setup form submits the whole property in one payload — profile, room
types and policies together — so these nest rather than exposing three
separate resources. A resort saved with rooms but no policies isn't a
state worth supporting.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.platform.models import PolicyCategory
from app.platform.users import UserRole


class RoomTypeIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    base_rate: Decimal = Field(ge=0, max_digits=10, decimal_places=2)
    max_occupancy: int = Field(ge=1, le=99, default=2)
    amenities: list[str] = Field(default_factory=list)

    @field_validator("amenities")
    @classmethod
    def _clean_amenities(cls, v: list[str]) -> list[str]:
        return [a.strip() for a in v if a and a.strip()]


class RoomTypeOut(RoomTypeIn):
    model_config = ConfigDict(from_attributes=True)

    id: int


class PolicyIn(BaseModel):
    category: PolicyCategory
    content_text: str = Field(min_length=1)


class PolicyOut(PolicyIn):
    model_config = ConfigDict(from_attributes=True)

    id: int
    updated_at: datetime


class HotelIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = None
    city: str | None = Field(default=None, max_length=120)
    address: str | None = None
    phone: str | None = Field(default=None, max_length=40)
    whatsapp_number: str | None = Field(default=None, max_length=40)
    currency: str = Field(default="NPR", min_length=3, max_length=3)
    timezone: str = Field(default="Asia/Kathmandu", max_length=64)
    room_types: list[RoomTypeIn] = Field(default_factory=list)
    policies: list[PolicyIn] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def _upper_currency(cls, v: str) -> str:
        return v.upper()


class HotelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    city: str | None
    address: str | None
    phone: str | None
    whatsapp_number: str | None
    currency: str
    timezone: str
    created_at: datetime
    room_types: list[RoomTypeOut]
    policies: list[PolicyOut]


class HotelSummary(BaseModel):
    """Listing shape — no nested children, so the list endpoint stays cheap."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    city: str | None
    created_at: datetime


# --- staff accounts ------------------------------------------------------


def normalise_email(value: str) -> str:
    """Lower-case and strip, everywhere an email is stored or looked up.

    Without this, "Admin@Rupakot.com" registers as a second account that
    the login form can never reach, because the form sends what the user
    typed and the unique index is case-sensitive.
    """
    return (value or "").strip().lower()


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=200)

    @field_validator("email")
    @classmethod
    def _normalise(cls, v: str) -> str:
        return normalise_email(v)


class UserOut(BaseModel):
    """Safe to return. Deliberately has no hashed_password field at all,
    rather than one that something later forgets to exclude."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str | None
    role: UserRole
    hotel_id: int | None
    is_active: bool
    last_login_at: datetime | None


class LoginOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_at: datetime
    user: UserOut


class SessionOut(BaseModel):
    """What GET /api/auth/me returns: the caller as the server sees them."""

    authenticated: bool
    method: str
    user: UserOut | None = None
    hotel_id: int | None = None
    role: str = ""
