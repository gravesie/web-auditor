"""Password hashing for the login wall.

Uses bcrypt directly rather than passlib, which has a version-detection break with
recent bcrypt releases. bcrypt caps the input at 72 bytes, so we truncate to that
before hashing and verifying, keeping the two consistent.
"""

from __future__ import annotations

import bcrypt

_MAX_BYTES = 72


def _prepared(password: str) -> bytes:
    return password.encode("utf-8")[:_MAX_BYTES]


def hash_password(password: str) -> str:
    """Return a bcrypt hash for storage in User.password_hash."""
    if not password:
        raise ValueError("password must not be empty")
    return bcrypt.hashpw(_prepared(password), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str | None) -> bool:
    """True when the password matches the stored hash. False on any bad input."""
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(_prepared(password), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False
