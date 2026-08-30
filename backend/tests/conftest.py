"""
Shared test fixtures.

Retrieval cannot be meaningfully faked: the whole point is to check that a
real embedding model plus a real pgvector query finds the right passage. So
these tests need Postgres. They run against the configured database but
never commit — everything happens inside one transaction that is rolled
back, so no fixture data survives the run.
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core import model_router
from app.core.config import settings
from app.platform.models import Hotel


def pytest_configure(config):
    config.addinivalue_line("markers", "requires_db: needs a live Postgres+pgvector")


@pytest.fixture(autouse=True)
def _no_billed_calls(monkeypatch):
    """Keep the whole suite off hosted providers unless explicitly opted in.

    A developer who has sourced .env would otherwise turn every test run into
    a billed API run, and the results would depend on a model's mood rather
    than on the code. Opt in with RUN_HOSTED_CHAT_EVAL=1 for the files that
    genuinely need a provider.
    """
    if os.environ.get("RUN_HOSTED_CHAT_EVAL") == "1":
        return
    monkeypatch.setattr(model_router, "CHAT_PROVIDER", "extractive")
    monkeypatch.setattr(model_router, "FAST_PROVIDER", "none")


@pytest_asyncio.fixture(scope="function")
async def db() -> AsyncSession:
    """A session bound to a transaction that is always rolled back."""
    engine = create_async_engine(settings.database_url)
    conn = await engine.connect()
    trans = await conn.begin()
    session = AsyncSession(bind=conn, expire_on_commit=False)
    try:
        yield session
    finally:
        await session.close()
        await trans.rollback()
        await conn.close()
        await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def hotel(db: AsyncSession) -> Hotel:
    """A throwaway hotel to scope fixture knowledge against."""
    h = Hotel(name="Eval Test Property", city="Pokhara")
    db.add(h)
    await db.flush()
    return h
