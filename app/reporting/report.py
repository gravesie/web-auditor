"""Render an audit run to a PDF.

Builds a print-styled HTML report from the run data (the same view the dashboard
uses) and turns it into a PDF with headless Chromium, which is already installed
for the audits. No extra PDF dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import jinja2
from playwright.sync_api import sync_playwright

from app.db import SessionLocal
from app.models import AuditRun, Site
from app.reporting.view import build_audit_view

_TEMPLATES = Path(__file__).parent / "templates"
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES)), autoescape=True
)


@dataclass
class Report:
    pdf: bytes
    filename: str
    domain: str
    site_score: float | None


def generate_report(run_id) -> Report:
    session = SessionLocal()
    try:
        run = session.get(AuditRun, run_id)
        if run is None:
            raise ValueError(f"audit run {run_id} not found")
        site = session.get(Site, run.site_id)
        audits = build_audit_view(session, run)
        html = _env.get_template("report.html").render(
            site=site, run=run, audits=audits, generated=datetime.now(UTC)
        )
        domain = site.domain
        score = run.site_score
        started = run.started_at
    finally:
        session.close()

    pdf = _html_to_pdf(html)
    filename = f"audit-{domain}-{started:%Y-%m-%d}.pdf"
    return Report(pdf=pdf, filename=filename, domain=domain, site_score=score)


def _html_to_pdf(html: str) -> bytes:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        pdf = page.pdf(
            format="A4",
            print_background=True,
            margin={"top": "12mm", "bottom": "12mm", "left": "10mm", "right": "10mm"},
        )
        browser.close()
    return pdf
