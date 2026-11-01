import uuid

import pytest
from fastapi import HTTPException

from app.core.config import Settings
from app.core.security import (create_access_token, decode_token, decode_token_data, hash_password,
                               verify_password)


def test_password_roundtrip():
    h = hash_password("correct horse")
    assert verify_password("correct horse", h) and not verify_password("wrong", h)


def test_long_passwords_are_fully_significant():
    # raw bcrypt ignores everything after byte 72; pre-hashing must not
    base = "a" * 80
    h = hash_password(base + "X")
    assert verify_password(base + "X", h) and not verify_password(base + "Y", h)


def test_verify_password_rejects_garbage_hash_instead_of_crashing():
    assert verify_password("x", "not-a-bcrypt-hash") is False


def test_token_roundtrip_and_tamper():
    uid = uuid.uuid4()
    token = create_access_token(uid)
    assert decode_token(token) == uid
    assert decode_token_data(token).jti
    with pytest.raises(HTTPException):
        decode_token(token + "x")


def test_production_refuses_default_secret():
    with pytest.raises(RuntimeError, match="JWT_SECRET"):
        Settings(environment="production", jwt_secret="change-me", storage_backend="s3").assert_safe_for_production()


def test_production_refuses_local_storage_and_wildcard_cors():
    s = Settings(environment="production", jwt_secret="x" * 40, storage_backend="local", cors_origins="*")
    with pytest.raises(RuntimeError) as e:
        s.assert_safe_for_production()
    assert "STORAGE_BACKEND" in str(e.value) and "CORS_ORIGINS" in str(e.value)


def test_development_is_not_blocked():
    Settings(environment="development", jwt_secret="change-me").assert_safe_for_production()
