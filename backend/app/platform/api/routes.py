"""
Platform API — hotel profile, room types and policies (Slice A, US-5).

SECURITY NOTE: these endpoints are currently unauthenticated and take the
hotel id straight from the URL. There is no users table or session yet, so
there is nothing to scope against. Every tenant-scoped read goes through
`_load_hotel` below — when auth lands, that one function becomes the place
the session's hotel_id is enforced, rather than trusting the caller.
Do not expose this API publicly before that happens (NFR-3).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.platform import models, schemas

router = APIRouter(prefix="/platform", tags=["platform"])


async def _load_hotel(hotel_id: int, db: AsyncSession) -> models.Hotel:
    """Fetch a hotel with children eagerly loaded, or 404.

    Children are eager-loaded because the async session cannot lazy-load
    on attribute access — serialising the response would raise otherwise.
    """
    result = await db.execute(
        select(models.Hotel)
        .where(models.Hotel.id == hotel_id)
        .options(
            selectinload(models.Hotel.room_types),
            selectinload(models.Hotel.policies),
        )
    )
    hotel = result.scalar_one_or_none()
    if hotel is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Hotel {hotel_id} not found",
        )
    return hotel


def _apply_children(hotel: models.Hotel, payload: schemas.HotelIn) -> None:
    """Replace room types and policies wholesale.

    The setup screen submits the full list every time, so replace rather
    than diff. delete-orphan on the relationship removes the old rows.
    """
    hotel.room_types = [
        models.RoomType(
            name=rt.name,
            description=rt.description,
            base_rate=rt.base_rate,
            max_occupancy=rt.max_occupancy,
            amenities=rt.amenities,
        )
        for rt in payload.room_types
    ]
    hotel.policies = [
        models.HotelPolicy(category=p.category, content_text=p.content_text)
        for p in payload.policies
    ]


@router.post(
    "/hotels",
    response_model=schemas.HotelOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_hotel(payload: schemas.HotelIn, db: AsyncSession = Depends(get_db)):
    """Create a property with its room types and policies in one transaction."""
    hotel = models.Hotel(
        **payload.model_dump(exclude={"room_types", "policies"})
    )
    _apply_children(hotel, payload)

    db.add(hotel)
    await db.commit()
    return await _load_hotel(hotel.id, db)


@router.get("/hotels", response_model=list[schemas.HotelSummary])
async def list_hotels(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(models.Hotel).order_by(models.Hotel.id))
    return list(result.scalars().all())


@router.get("/hotels/{hotel_id}", response_model=schemas.HotelOut)
async def get_hotel(hotel_id: int, db: AsyncSession = Depends(get_db)):
    return await _load_hotel(hotel_id, db)


@router.put("/hotels/{hotel_id}", response_model=schemas.HotelOut)
async def update_hotel(
    hotel_id: int, payload: schemas.HotelIn, db: AsyncSession = Depends(get_db)
):
    """Update the profile and replace room types and policies."""
    hotel = await _load_hotel(hotel_id, db)

    for field, value in payload.model_dump(
        exclude={"room_types", "policies"}
    ).items():
        setattr(hotel, field, value)
    _apply_children(hotel, payload)

    await db.commit()
    return await _load_hotel(hotel_id, db)
