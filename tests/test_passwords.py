"""Password hashing."""

from __future__ import annotations

import pytest

from app.security.passwords import hash_password, verify_password


def test_hash_and_verify_roundtrip():
    h = hash_password("correct horse battery staple")
    assert verify_password("correct horse battery staple", h)


def test_wrong_password_fails():
    h = hash_password("s3cret-value")
    assert not verify_password("s3cret-valuX", h)


def test_hash_is_salted():
    assert hash_password("same") != hash_password("same")


def test_empty_inputs_are_rejected():
    with pytest.raises(ValueError):
        hash_password("")
    assert not verify_password("", hash_password("x"))
    assert not verify_password("x", None)
    assert not verify_password("x", "not-a-bcrypt-hash")


def test_long_password_truncated_consistently():
    # bcrypt caps at 72 bytes; the first 72 must still verify.
    base = "a" * 72
    h = hash_password(base)
    assert verify_password(base + "extra ignored", h)
