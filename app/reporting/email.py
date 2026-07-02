"""Email the PDF report.

Sends via Resend when an API key is configured. If there's no key, or the send
fails (for example the From domain isn't verified yet), the email is written to the
outbox as a .eml file with the PDF attached, ready to open and send by hand. Same
report, same message; only the transport differs.
"""

from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

from app.config import settings
from app.mailer import send_email
from app.reporting.report import Report, generate_report


def _subject(report: Report) -> str:
    if report.site_score is None:
        return f"Goyande AI website audit: {report.domain}"
    return f"Goyande AI website audit: {report.domain} ({report.site_score:.0f}/100)"


def _body(report: Report) -> str:
    score = "n/a" if report.site_score is None else f"{report.site_score:.1f}/100"
    return (
        f"Goyande AI website audit for {report.domain} is attached.\n\n"
        f"Overall site score: {score}\n"
    )


def _send_via_resend(report: Report, subject: str, body: str) -> tuple[bool, str]:
    attachment = {
        "filename": report.filename,
        "content": base64.b64encode(report.pdf).decode("ascii"),
    }
    return send_email(settings.email_to, subject, body, attachments=[attachment])


def _write_to_outbox(report: Report, subject: str, body: str) -> Path:
    message = EmailMessage()
    message["To"] = settings.email_to
    message["From"] = settings.email_from
    message["Subject"] = subject
    message.set_content(body)
    message.add_attachment(
        report.pdf, maintype="application", subtype="pdf", filename=report.filename
    )

    outbox = Path(settings.outbox_dir)
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", report.domain)
    path = outbox / f"{stamp}-{safe}.eml"
    path.write_bytes(bytes(message))
    return path


def email_report(run_id) -> dict:
    """Generate the report PDF and deliver it. Returns a result summary."""
    report = generate_report(run_id)
    subject = _subject(report)
    body = _body(report)

    if settings.resend_api_key:
        ok, detail = _send_via_resend(report, subject, body)
        if ok:
            return {"method": "resend", "to": settings.email_to, "detail": detail}
        path = _write_to_outbox(report, subject, body)
        return {
            "method": "outbox",
            "to": settings.email_to,
            "detail": f"Resend send failed ({detail}); wrote {path}",
            "path": str(path),
        }

    path = _write_to_outbox(report, subject, body)
    return {"method": "outbox", "to": settings.email_to, "path": str(path), "detail": str(path)}
