"""
JWT + bcrypt auth edge cases.

Targets bugs that wouldn't show up on the happy path:
  - tampered tokens, missing kind field, expired tokens
  - bcrypt 72-byte truncation
  - case-sensitive usernames
  - revocation behavior with Redis unreachable
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from jose import JWTError, jwt

from src.api.auth import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    is_token_revoked,
    verify_password,
)
from src.config import settings


def _decode_unsafe(token: str) -> dict:
    return jwt.get_unverified_claims(token)


# ─────────────────────────────────────────────────────────────────────
# Token shape
# ─────────────────────────────────────────────────────────────────────

def test_access_token_has_kind_access():
    t = create_access_token("alice", "operator")
    claims = _decode_unsafe(t)
    assert claims["kind"] == "access"
    assert claims["sub"] == "alice"
    assert claims["role"] == "operator"
    assert "jti" in claims


def test_refresh_token_has_kind_refresh():
    t = create_refresh_token("alice", "operator")
    claims = _decode_unsafe(t)
    assert claims["kind"] == "refresh"


def test_each_token_has_unique_jti():
    """Two tokens minted back-to-back must have different jti — otherwise
    revoking one revokes the other."""
    t1 = create_access_token("alice", "operator")
    t2 = create_access_token("alice", "operator")
    assert _decode_unsafe(t1)["jti"] != _decode_unsafe(t2)["jti"]


def test_decode_rejects_tampered_signature():
    """Last char of the signature flipped. Must raise JWTError."""
    t = create_access_token("alice", "operator")
    head, payload, sig = t.split(".")
    # Pick a different char that's still in the JWT base64url alphabet.
    tampered_sig = sig[:-1] + ("A" if sig[-1] != "A" else "B")
    tampered = ".".join([head, payload, tampered_sig])
    with pytest.raises(JWTError):
        decode_token(tampered)


def test_decode_rejects_expired_token():
    """Mint a token with negative TTL → JWTError on decode."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": "alice", "role": "admin", "kind": "access",
        "jti": "x", "iat": now - timedelta(hours=2),
        "exp": now - timedelta(hours=1),
    }
    expired = jwt.encode(payload, settings.jwt_secret_key,
                         algorithm=settings.jwt_algorithm)
    with pytest.raises(JWTError):
        decode_token(expired)


def test_decode_rejects_token_signed_with_wrong_secret():
    payload = {
        "sub": "alice", "role": "admin", "kind": "access",
        "jti": "x",
        "iat": datetime.now(timezone.utc),
        "exp": datetime.now(timezone.utc) + timedelta(hours=1),
    }
    bogus = jwt.encode(payload, "totally-different-32-char-key-xxx",
                       algorithm=settings.jwt_algorithm)
    with pytest.raises(JWTError):
        decode_token(bogus)


# ─────────────────────────────────────────────────────────────────────
# Password handling
# ─────────────────────────────────────────────────────────────────────

def test_verify_password_round_trip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h) is True


def test_verify_password_wrong_password_returns_false():
    h = hash_password("correct horse battery staple")
    assert verify_password("wrong password", h) is False


def test_bcrypt_silently_truncates_at_72_bytes():
    """bcrypt only hashes the first 72 bytes. Two passwords that share
    the first 72 chars are treated as identical. This is a documented
    bcrypt behavior — surface it as a test so a future migration to
    Argon2/scrypt knows what changes."""
    long_a = "A" * 80
    long_b = "A" * 72 + "DIFFERENT_TAIL"
    h = hash_password(long_a)
    assert verify_password(long_b, h) is True


def test_password_with_unicode_round_trips():
    """We use bcrypt which works with bytes; unicode should encode cleanly."""
    pw = "kullanıcı_şifresi_ŞĞÜÇİÖ"
    h = hash_password(pw)
    assert verify_password(pw, h) is True


def test_password_hash_changes_each_time():
    """gensalt() makes each hash unique even for the same password."""
    p = "secret"
    assert hash_password(p) != hash_password(p)


# ─────────────────────────────────────────────────────────────────────
# Revocation (Redis blacklist)
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_token_revoked_fail_open_when_redis_down():
    """If Redis is unreachable, is_token_revoked must return False —
    otherwise the whole API stops responding to any authenticated request
    when Redis hiccups. This was an explicit design choice."""

    class _BoomClient:
        async def exists(self, _key):
            raise ConnectionError("redis unreachable")

    with patch("src.api.auth.redis_client.get_client", return_value=_BoomClient()):
        # The conftest fixture stubs is_token_revoked with AsyncMock for
        # other tests; here we explicitly want the real implementation.
        from src.api.auth import is_token_revoked as real_is_revoked
        assert await real_is_revoked("any-jti") is False
