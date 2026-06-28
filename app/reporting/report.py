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

from app import __version__
from app.db import SessionLocal
from app.models import AuditRun, Site
from app.reporting.view import build_audit_view

_TEMPLATES = Path(__file__).parent / "templates"
_ASSETS = Path(__file__).parent / "assets"
_env = jinja2.Environment(
    loader=jinja2.FileSystemLoader(str(_TEMPLATES)), autoescape=True
)

# Goyande horizontal lockup, inlined into the report masthead.
_LOGO_SVG = (_ASSETS / "goyande-lockup-horizontal.svg").read_text(encoding="utf-8")

# Repeating page footer (Chromium fills the pageNumber/totalPages spans). Header is
# empty because the branded masthead lives in the body, on page one.
_HEADER_TEMPLATE = "<div></div>"
_FOOTER_TEMPLATE = (
    '<div style="font-size:8px; width:100%; box-sizing:border-box; padding:4px 12mm 0;'
    " color:#475569; border-top:1px solid #d1d5db;"
    " font-family:'Segoe UI',system-ui,sans-serif; display:flex;"
    ' justify-content:space-between; align-items:center;">'
    '<span><span style="color:#102A4C; font-weight:600;">goyande.ai</span>'
    f"&nbsp;&middot;&nbsp;v{__version__}</span>"
    "<span>Indicative audit for guidance only; not legal or professional advice.</span>"
    "<span>&copy; 2026 Peter Graves trading as Goyande AI"
    "&nbsp;&nbsp;&middot;&nbsp;&nbsp;"
    'Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>'
    "</div>"
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
            site=site, run=run, audits=audits, generated=datetime.now(UTC), logo_svg=_LOGO_SVG
        )
        domain = site.domain
        score = run.site_score
        started = run.started_at
    finally:
        session.close()

    pdf = _html_to_pdf(html)
    filename = f"Goyande-AI-website-audit-{domain}-{started:%Y-%m-%d}.pdf"
    return Report(pdf=pdf, filename=filename, domain=domain, site_score=score)


def _html_to_pdf(html: str) -> bytes:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.set_content(html, wait_until="load")
        pdf = page.pdf(
            format="A4",
            print_background=True,
            display_header_footer=True,
            header_template=_HEADER_TEMPLATE,
            footer_template=_FOOTER_TEMPLATE,
            margin={"top": "12mm", "bottom": "18mm", "left": "10mm", "right": "10mm"},
        )
        browser.close()
    return pdf
