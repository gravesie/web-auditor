"""Symmetric encryption for stored secrets, keyed off settings.secret_key.

Used for two things: encrypting OAuth refresh tokens before they go in the
database (Connection.credentials_encrypted), and signing/encrypting the short-lived
OAuth `state` value so the callback can trust what it gets back.

The Fernet key is derived from secret_key, so rotating secret_key invalidates every
stored token (each affected connection then has to be reconnected) and any in-flight
OAuth state. That is the intended trade-off: one secret to manage, no separate key
file. Override secret_key for any non-local deployment.
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class DecryptionError(Exception):
    """Raised when a value cannot be decrypted (wrong key, tampered, or corrupt)."""


def _fernet() -> Fernet:
    # Fernet needs a 32-byte url-safe base64 key. Derive one deterministically from
    # secret_key so we never persist a separate key.
    digest = hashlib.sha256(settings.secret_key.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt(plaintext: str) -> str:
    """Encrypt a string, returning a url-safe token suitable for storage."""
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(token: str) -> str:
    """Decrypt a token produced by encrypt(). Raises DecryptionError on any failure."""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise DecryptionError("could not decrypt value") from exc


def decrypt_ttl(token: str, max_age_seconds: int) -> str:
    """Decrypt a token, rejecting it if older than max_age_seconds.

    Fernet timestamps every token, so this also expires stale values (used for the
    short-lived OAuth state).
    """
    try:
        return _fernet().decrypt(token.encode("ascii"), ttl=max_age_seconds).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise DecryptionError("could not decrypt value (invalid or expired)") from exc


def encrypt_json(payload: dict[str, Any]) -> str:
    """Encrypt a JSON-serialisable dict."""
    return encrypt(json.dumps(payload, separators=(",", ":")))


def decrypt_json(token: str) -> dict[str, Any]:
    """Decrypt a token produced by encrypt_json() back into a dict."""
    return _loads_object(decrypt(token))


def decrypt_json_ttl(token: str, max_age_seconds: int) -> dict[str, Any]:
    """Decrypt a JSON token, rejecting it if older than max_age_seconds."""
    return _loads_object(decrypt_ttl(token, max_age_seconds))


def _loads_object(raw: str) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise DecryptionError("decrypted payload was not a JSON object")
    return data
