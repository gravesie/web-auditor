"""Shared email transport (Resend).

One place that knows how to post an email to Resend, used by the magic-link sign-in
and the PDF report sender. Returns (ok, detail) and never raises, so callers decide
how to handle a failure (retry, outbox, or surface a message).
"""

from __future__ import annotations

import httpx

from app.config import settings

_RESEND_ENDPOINT = "https://api.resend.com/emails"


def send_email(
    to: str, subject: str, text: str, attachments: list[dict] | None = None
) -> tuple[bool, str]:
    """Send a plain-text email via Resend. attachments are [{filename, content(base64)}]."""
    if not settings.resend_api_key:
        return False, "no Resend API key configured"
    payload: dict = {
        "from": settings.email_from,
        "to": [to],
        "subject": subject,
        "text": text,
    }
    if attachments:
        payload["attachments"] = attachments
    try:
        resp = httpx.post(
            _RESEND_ENDPOINT,
            json=payload,
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {settings.resend_api_key}",
                "Content-Type": "application/json",
            },
        )
    except httpx.HTTPError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if resp.status_code in (200, 201):
        return True, resp.json().get("id", "sent")
    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
