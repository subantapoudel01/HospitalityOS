"""
Password hashing and token handling, with no database or HTTP involved.

The properties here are the ones that make the difference between "there is
a login" and "there is authentication": that a hash is not reversible or
repeatable, that a token cannot be edited by its holder, and that expiry is
actually enforced rather than merely recorded.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from app.core import security
from app.core.config import settings

SECRET = "test-secret-not-used-anywhere-real"


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", SECRET)
    monkeypatch.setattr(settings, "jwt_expire_minutes", 60)


# --- passwords -----------------------------------------------------------


def test_hash_is_not_the_password():
    hashed = security.hash_password("correct horse battery staple")
    assert "correct horse" not in hashed
    assert hashed.startswith("$2")  # bcrypt


def test_same_password_hashes_differently_each_time():
    """Per-hash salt. Identical hashes would reveal that two staff share
    a password just by looking at the table."""
    a = security.hash_password("same-password-12")
    b = security.hash_password("same-password-12")
    assert a != b
    assert security.verify_password("same-password-12", a)
    assert security.verify_password("same-password-12", b)


def test_wrong_password_is_rejected():
    hashed = security.hash_password("the-real-one-123")
    assert not security.verify_password("the-real-one-124", hashed)
    assert not security.verify_password("", hashed)


def test_corrupt_hash_reads_as_wrong_password_not_a_crash():
    """A garbled row must not 500. A 500 where a 401 belongs tells an
    attacker the account exists."""
    assert security.verify_password("anything", "not-a-bcrypt-hash") is False
    assert security.verify_password("anything", "") is False


def test_overlong_password_is_refused_rather_than_truncated():
    """bcrypt silently ignores everything past 72 bytes. Accepting the
    input would make two different long passwords the same credential."""
    with pytest.raises(ValueError):
        security.hash_password("x" * 73)


def test_seventy_two_bytes_is_still_allowed():
    assert security.hash_password("x" * 72)


# --- tokens --------------------------------------------------------------


def _issue(**overrides):
    kwargs = {
        "user_id": 7,
        "email": "admin@rupakot.com",
        "role": "admin",
        "hotel_id": 1,
    }
    kwargs.update(overrides)
    return security.issue_token(**kwargs)


def test_round_trip_preserves_the_claims():
    token, expires_at = _issue()
    claims = security.decode_token(token)
    assert claims.user_id == 7
    assert claims.email == "admin@rupakot.com"
    assert claims.role == "admin"
    assert claims.hotel_id == 1
    assert abs((claims.expires_at - expires_at).total_seconds()) < 2


def test_platform_admin_may_carry_a_null_hotel():
    token, _ = _issue(hotel_id=None, role="platform_admin")
    assert security.decode_token(token).hotel_id is None


def test_a_tampered_payload_is_rejected():
    """The whole point. Someone editing hotel_id to read another
    property's transcripts must not get past the signature."""
    token, _ = _issue(hotel_id=1)
    header, payload, signature = token.split(".")
    forged = jwt.encode(
        {"sub": "7", "email": "x", "role": "admin", "hotel_id": 999,
         "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())},
        "a-different-secret",
        algorithm="HS256",
    )
    with pytest.raises(security.InvalidToken):
        security.decode_token(forged)
    # ...and swapping just the payload of a real token fails too.
    with pytest.raises(security.InvalidToken):
        security.decode_token(f"{header}.{payload}x.{signature}")


def test_alg_none_is_rejected():
    """The classic JWT forgery: an unsigned token that claims it needs no
    signature. Rejected because decode pins algorithms explicitly."""
    unsigned = jwt.encode(
        {"sub": "7", "hotel_id": 999,
         "exp": int((datetime.now(timezone.utc) + timedelta(hours=1)).timestamp())},
        key="",
        algorithm="none",
    )
    with pytest.raises(security.InvalidToken):
        security.decode_token(unsigned)


def test_an_expired_token_is_rejected():
    expired = jwt.encode(
        {"sub": "7", "role": "admin", "hotel_id": 1,
         "exp": int((datetime.now(timezone.utc) - timedelta(minutes=1)).timestamp())},
        SECRET,
        algorithm="HS256",
    )
    with pytest.raises(security.InvalidToken):
        security.decode_token(expired)


def test_a_token_without_expiry_is_rejected():
    """A token that never expires is a permanent credential handed to a
    browser. Refuse it even though it is correctly signed."""
    forever = jwt.encode({"sub": "7", "role": "admin"}, SECRET, algorithm="HS256")
    with pytest.raises(security.InvalidToken):
        security.decode_token(forever)


def test_a_token_signed_with_a_rotated_secret_is_rejected(monkeypatch):
    token, _ = _issue()
    monkeypatch.setattr(settings, "jwt_secret", "rotated-secret-value")
    with pytest.raises(security.InvalidToken):
        security.decode_token(token)


# --- fails closed --------------------------------------------------------


def test_no_secret_means_no_tokens_at_all(monkeypatch):
    """Not a default secret, not a warning - a refusal. A shipped default
    signing key is forgeable by anyone who can read the repo."""
    monkeypatch.setattr(settings, "jwt_secret", "")
    assert security.auth_configured() is False
    with pytest.raises(security.SecurityNotConfigured):
        _issue()
    with pytest.raises(security.SecurityNotConfigured):
        security.decode_token("anything")


def test_whitespace_only_secret_counts_as_unset(monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "   ")
    assert security.auth_configured() is False
    with pytest.raises(security.SecurityNotConfigured):
        _issue()
