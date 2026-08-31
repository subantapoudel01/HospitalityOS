"""
Staff login over HTTP, and the tenant boundary it finally makes possible.

The shared token could not enforce a boundary: it carried no identity, so
every holder saw every property. These tests exist to prove the replacement
actually does, and that the login form is not an account enumerator.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.auth import COOKIE_NAME
from app.core.config import settings
from app.core.db import get_db
from app.core.security import hash_password
from app.main import app
from app.platform.models import Hotel
from app.platform.users import User, UserRole

JWT_SECRET = "test-jwt-secret-for-the-login-suite"
PASSWORD = "a-real-password-42"


@pytest.fixture
def client():
    """TestClient with its own session per request.

    It cannot share the `db` fixture: TestClient drives the app on its own
    event loop, and an asyncpg connection opened on pytest-asyncio's loop
    fails when awaited from another one.
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    async def _override():
        engine = create_async_engine(settings.database_url)
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


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", JWT_SECRET)
    monkeypatch.setattr(settings, "jwt_expire_minutes", 60)
    # Off by default: these tests are about the JWT path, and leaving the
    # shared token enabled would let a test pass through the wrong door.
    monkeypatch.setattr(settings, "staff_api_token", "")


@pytest.fixture
def accounts():
    """Two hotels, one user each. Committed, then removed afterwards.

    Committed on purpose: the request under test runs on the TestClient's
    own session and cannot see an uncommitted fixture row.
    """
    import asyncio

    from sqlalchemy import delete
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    # Not a .test/.invalid domain: email-validator rejects reserved TLDs
    # outright, which would 422 every login here for the wrong reason.
    suffix = uuid.uuid4().hex[:8]
    created: dict[str, object] = {}

    async def _setup():
        engine = create_async_engine(settings.database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            mine = Hotel(name=f"Rupakot Test {suffix}", city="Pokhara")
            theirs = Hotel(name=f"Other Property {suffix}", city="Pokhara")
            db.add_all([mine, theirs])
            await db.flush()

            user = User(
                email=f"admin-{suffix}@rupakot-fixture.com",
                hashed_password=hash_password(PASSWORD),
                full_name="Test Admin",
                hotel_id=mine.id,
                role=UserRole.admin,
            )
            disabled = User(
                email=f"gone-{suffix}@rupakot-fixture.com",
                hashed_password=hash_password(PASSWORD),
                hotel_id=mine.id,
                role=UserRole.staff,
                is_active=False,
            )
            db.add_all([user, disabled])
            await db.commit()
            created.update(
                email=user.email,
                disabled_email=disabled.email,
                user_id=user.id,
                disabled_id=disabled.id,
                my_hotel=mine.id,
                their_hotel=theirs.id,
            )
        await engine.dispose()

    async def _teardown():
        engine = create_async_engine(settings.database_url)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as db:
            await db.execute(
                delete(User).where(
                    User.id.in_([created["user_id"], created["disabled_id"]])
                )
            )
            await db.execute(
                delete(Hotel).where(
                    Hotel.id.in_([created["my_hotel"], created["their_hotel"]])
                )
            )
            await db.commit()
        await engine.dispose()

    asyncio.run(_setup())
    yield created
    asyncio.run(_teardown())


def _login(client, email, password=PASSWORD):
    return client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )


# --- the happy path ------------------------------------------------------


def test_correct_credentials_return_a_token_and_a_cookie(client, accounts):
    res = _login(client, accounts["email"])
    assert res.status_code == 200, res.text

    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["email"] == accounts["email"]
    assert body["user"]["hotel_id"] == accounts["my_hotel"]
    assert COOKIE_NAME in res.cookies


def test_the_response_never_leaks_the_credential(client, accounts):
    """UserOut has no hashed_password field at all, rather than one that
    something later forgets to exclude."""
    res = _login(client, accounts["email"])
    assert "hashed_password" not in res.json()["user"]
    # The whole body, not just the user object: a hash echoed from any
    # nested field is the same leak.
    assert "hashed_password" not in res.text
    assert PASSWORD not in res.text
    assert "$2b$" not in res.text


def test_the_token_opens_the_staff_api(client, accounts):
    token = _login(client, accounts["email"]).json()["access_token"]
    res = client.get(
        f"/api/receptionist/staff/metrics?hotel_id={accounts['my_hotel']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 200


def test_the_cookie_alone_opens_the_staff_api(client, accounts):
    """Next.js middleware gates on the cookie; the API must honour it too,
    or the two disagree about who is signed in."""
    token = _login(client, accounts["email"]).json()["access_token"]
    res = client.get(
        f"/api/receptionist/staff/metrics?hotel_id={accounts['my_hotel']}",
        cookies={COOKIE_NAME: token},
    )
    assert res.status_code == 200


def test_me_reports_the_signed_in_user(client, accounts):
    token = _login(client, accounts["email"]).json()["access_token"]
    body = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).json()
    assert body["authenticated"] is True
    assert body["method"] == "jwt"
    assert body["user"]["email"] == accounts["email"]
    assert body["hotel_id"] == accounts["my_hotel"]


# --- the tenant boundary -------------------------------------------------


def test_a_user_cannot_read_another_hotel(client, accounts):
    """The reason accounts exist. With the shared token this was allowed."""
    token = _login(client, accounts["email"]).json()["access_token"]
    res = client.get(
        f"/api/receptionist/staff/conversations?hotel_id={accounts['their_hotel']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404, (
        "a staff member must not read another property's guest transcripts"
    )


def test_the_refusal_does_not_confirm_the_other_hotel_exists(client, accounts):
    """404 not 403, and the same 404 for a hotel id that was never real -
    otherwise the dashboard enumerates every property on the platform."""
    token = _login(client, accounts["email"]).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    real_but_not_mine = client.get(
        f"/api/receptionist/staff/conversations?hotel_id={accounts['their_hotel']}",
        headers=headers,
    )
    never_existed = client.get(
        "/api/receptionist/staff/conversations?hotel_id=88888888", headers=headers
    )
    assert real_but_not_mine.status_code == never_existed.status_code == 404

    # The bodies differ only by the id the CALLER supplied, which tells
    # them nothing they did not already know. What must not differ is
    # anything derived from whether the hotel actually exists.
    def blank_the_echoed_id(response, hotel_id):
        return response.json()["detail"].replace(str(hotel_id), "<id>")

    assert blank_the_echoed_id(
        real_but_not_mine, accounts["their_hotel"]
    ) == blank_the_echoed_id(never_existed, 88888888)


def test_tenant_scope_applies_to_body_endpoints_too(client, accounts):
    """The check must not live only on the query-string routes."""
    token = _login(client, accounts["email"]).json()["access_token"]
    res = client.patch(
        "/api/receptionist/staff/conversations/1",
        json={"hotel_id": accounts["their_hotel"], "status": "resolved"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404


# --- failure modes -------------------------------------------------------


def test_wrong_password_is_rejected(client, accounts):
    assert _login(client, accounts["email"], "wrong-password").status_code == 401


def test_unknown_email_and_wrong_password_are_indistinguishable(client, accounts):
    """Different messages here turn the form into an account enumerator."""
    unknown = _login(client, f"nobody-{uuid.uuid4().hex[:6]}@rupakot-fixture.com")
    wrong = _login(client, accounts["email"], "wrong-password")
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_a_deactivated_account_cannot_sign_in(client, accounts):
    res = _login(client, accounts["disabled_email"])
    assert res.status_code == 403


def test_email_is_case_insensitive(client, accounts):
    """Staff type their address however they like; the unique index is
    case-sensitive, so the lookup must normalise."""
    assert _login(client, accounts["email"].upper()).status_code == 200


def test_a_garbage_token_is_rejected(client, accounts):
    res = client.get(
        f"/api/receptionist/staff/metrics?hotel_id={accounts['my_hotel']}",
        headers={"Authorization": "Bearer not.a.jwt"},
    )
    assert res.status_code == 401


def test_no_credentials_at_all_is_rejected(client, accounts):
    res = client.get(
        f"/api/receptionist/staff/metrics?hotel_id={accounts['my_hotel']}"
    )
    assert res.status_code == 401


# --- fails closed --------------------------------------------------------


def test_unconfigured_secret_disables_login(client, accounts, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "")
    assert _login(client, accounts["email"]).status_code == 503


def test_unconfigured_everything_disables_the_staff_api(client, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "")
    monkeypatch.setattr(settings, "staff_api_token", "")
    res = client.get("/api/receptionist/staff/metrics?hotel_id=1")
    assert res.status_code == 503, "an unconfigured gate must fail closed"


# --- the legacy door -----------------------------------------------------


def test_a_jwt_wins_over_a_shared_token(client, accounts, monkeypatch):
    """Both credentials present must resolve to the scoped identity, not
    to the cross-tenant shared secret. Otherwise upgrading a deployment
    silently widens every staff session."""
    monkeypatch.setattr(settings, "staff_api_token", "legacy-token")
    token = _login(client, accounts["email"]).json()["access_token"]

    res = client.get(
        f"/api/receptionist/staff/conversations?hotel_id={accounts['their_hotel']}",
        headers={
            "Authorization": f"Bearer {token}",
            "X-Staff-Token": "legacy-token",
        },
    )
    assert res.status_code == 404, (
        "the shared token must not re-widen a scoped session"
    )


def test_the_shared_token_still_works_for_an_in_place_upgrade(
    client, accounts, monkeypatch
):
    monkeypatch.setattr(settings, "staff_api_token", "legacy-token")
    res = client.get(
        f"/api/receptionist/staff/metrics?hotel_id={accounts['my_hotel']}",
        headers={"X-Staff-Token": "legacy-token"},
    )
    assert res.status_code == 200


def test_guest_chat_is_still_open(client):
    """Auth must not lock guests out of the widget."""
    res = client.post(
        "/api/receptionist/chat", json={"hotel_id": 999999, "message": "hello"}
    )
    assert res.status_code == 404  # reached the handler, not the gate
