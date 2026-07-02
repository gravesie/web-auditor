"""Magic-link sign-in for customers.

Customers never set a password (that's staff only). They get a signed, time-limited
link by email that signs them into their own account. The token is a Fernet value
(app/security/crypto) carrying the user id, so it can't be forged or read, and it
expires. Reuses the shared secret_key and the shared mailer.
"""

from __future__ import annotations

import json

from app.config import settings
from app.mailer import send_email
from app.models import User
from app.security import crypto

# Links stay valid for a week: long enough to be useful from an email, short enough
# to limit exposure if a mailbox is compromised.
MAGIC_MAX_AGE_SECONDS = 7 * 24 * 60 * 60


def make_magic_token(user: User) -> str:
    return crypto.encrypt(json.dumps({"user_id": str(user.id)}))


def verify_magic_token(token: str, max_age: int = MAGIC_MAX_AGE_SECONDS) -> str | None:
    """The user id inside a valid, unexpired token, or None if invalid or expired."""
    try:
        data = json.loads(crypto.decrypt_ttl(token, max_age))
    except (crypto.DecryptionError, ValueError):
        return None
    user_id = data.get("user_id")
    return user_id if isinstance(user_id, str) else None


def magic_url(user: User) -> str:
    return f"{settings.base_url.rstrip('/')}/magic/{make_magic_token(user)}"


def send_magic_link(user: User, purpose: str = "view your website audit") -> tuple[bool, str]:
    """Email the user a sign-in link. Returns (ok, detail) from the mailer."""
    url = magic_url(user)
    subject = "Your Goyande website audit"
    body = (
        f"Hi,\n\nHere is your secure link to {purpose}:\n\n{url}\n\n"
        "The link works for 7 days. If you didn't request this, you can ignore it.\n\n"
        "— Goyande"
    )
    return send_email(user.email, subject, body)
