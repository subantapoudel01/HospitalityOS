"""
Async SQLAlchemy engine/session setup, shared by every module.

Models bind to `Base` from wherever they live — app/platform/ for shared
entities (hotels, rooms, users), app/modules/<module>/models/ for
module-owned ones — and are created once the first migration is written
(Slice A).
"""
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
