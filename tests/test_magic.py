"""Magic-link token signing and verification."""

from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

from app.magic import make_magic_token, verify_magic_token


def _user():
    return SimpleNamespace(id=uuid.uuid4())


def test_roundtrip():
    user = _user()
    token = make_magic_token(user)
    assert verify_magic_token(token) == str(user.id)


def test_garbage_token_is_rejected():
    assert verify_magic_token("not-a-real-token") is None
    assert verify_magic_token("") is None


def test_tampered_token_is_rejected():
    token = make_magic_token(_user())
    tampered = token[:-4] + ("aaaa" if token[-4:] != "aaaa" else "bbbb")
    assert verify_magic_token(tampered) is None


def test_expired_token_is_rejected():
    # Fernet's TTL is integer-second, so allow a clear margin past max_age=1.
    token = make_magic_token(_user())
    time.sleep(2.2)
    assert verify_magic_token(token, max_age=1) is None
