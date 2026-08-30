"""
The staff gate, exercised over HTTP.

These matter more than most tests here: behind this gate sit complete guest
conversation transcripts. A gate that fails open is worse than no gate,
because it looks like protection.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.db import get_db
from app.main import app

TOKEN = "test-staff-token"
GATED = [
    ("GET", "/api/receptionist/staff/metrics?hotel_id=1", None),
    ("GET", "/api/receptionist/staff/conversations?hotel_id=1", None),
    ("GET", "/api/receptionist/staff/booking-inquiries?hotel_id=1", None),
    ("PATCH", "/api/receptionist/staff/conversations/1",
     {"hotel_id": 1, "status": "resolved"}),
    ("POST", "/api/receptionist/staff/conversations/1/messages",
     {"hotel_id": 1, "content": "hello"}),
    ("PATCH", "/api/receptionist/staff/booking-inquiries/1",
     {"hotel_id": 1, "status": "contacted"}),
]


@pytest.fixture
def client():
    """TestClient with its own database session per request.

    It cannot share the `db` fixture's session: TestClient drives the app on
    its own event loop, and an asyncpg connection created on pytest-asyncio's
    loop blows up when awaited from another one. So these tests assert gate
    behaviour and never depend on fixture rows.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import settings as app_settings

    async def _override():
        engine = create_async_engine(app_settings.database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session:
                yield session
        finally:
            await engine.dispose()

    app.dependency_overrides[get_db] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(get_db, None)


@pytest.fixture
def with_token(monkeypatch):
    monkeypatch.setattr(settings, "staff_api_token", TOKEN)


def _call(client, method, path, body):
    return client.request(method, path, json=body)


@pytest.mark.parametrize("method,path,body", GATED)
def test_no_token_is_rejected(client, with_token, method, path, body):
    assert _call(client, method, path, body).status_code == 401


@pytest.mark.parametrize("method,path,body", GATED)
def test_wrong_token_is_rejected(client, with_token, method, path, body):
    res = client.request(
        method, path, json=body, headers={"X-Staff-Token": "not-the-token"}
    )
    assert res.status_code == 401


@pytest.mark.parametrize("method,path,body", GATED)
def test_unconfigured_server_disables_the_api(
    client, monkeypatch, method, path, body
):
    """Empty token must disable the endpoints, never open them."""
    monkeypatch.setattr(settings, "staff_api_token", "")
    res = client.request(
        method, path, json=body, headers={"X-Staff-Token": "anything"}
    )
    assert res.status_code == 503, "an unconfigured gate must fail closed"


def test_correct_token_is_accepted(client, with_token):
    # Any hotel id works: metrics over a hotel with no rows is zeros, which
    # is enough to prove the request passed the gate and reached the handler.
    res = client.get(
        "/api/receptionist/staff/metrics?hotel_id=987654",
        headers={"X-Staff-Token": TOKEN},
    )
    assert res.status_code == 200
    assert set(res.json()) == {
        "conversations_total",
        "escalated",
        "resolved",
        "inquiries_new",
    }


def test_guest_chat_is_not_gated(client, with_token):
    """The gate must not lock guests out of the widget."""
    res = client.post(
        "/api/receptionist/chat",
        json={"hotel_id": 999999, "message": "hello"},
    )
    # 404 for the unknown hotel - reached the handler, was not rejected at
    # the gate.
    assert res.status_code == 404
