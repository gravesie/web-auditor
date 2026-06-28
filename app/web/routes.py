"""Server-rendered dashboard views: site list and run drill-down."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from app.db import get_session
from app.models import AuditRun, Site
from app.reporting.view import build_audit_view, build_comparison

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _latest_run(session: Session, site_id: UUID) -> AuditRun | None:
    return session.execute(
        select(AuditRun)
        .where(AuditRun.site_id == site_id)
        .order_by(AuditRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    sites = session.execute(select(Site).order_by(Site.created_at.desc())).scalars().all()
    rows = [{"site": site, "run": _latest_run(session, site.id)} for site in sites]
    return templates.TemplateResponse(request, "dashboard.html", {"rows": rows})


@router.get("/sites/{site_id}", response_class=HTMLResponse)
def site_detail(
    site_id: UUID, request: Request, session: Session = Depends(get_session)
) -> HTMLResponse:
    site = session.get(Site, site_id)
    if site is None:
        return HTMLResponse("Site not found", status_code=404)

    runs = session.execute(
        select(AuditRun)
        .where(AuditRun.site_id == site_id)
        .order_by(AuditRun.started_at.desc())
    ).scalars().all()
    latest = runs[0] if runs else None
    previous = runs[1] if len(runs) > 1 else None
    audits = build_audit_view(session, latest) if latest is not None else []

    site_delta = None
    changed_findings: list[dict] = []
    if latest is not None and previous is not None:
        changed_findings = build_comparison(session, audits, previous)["changed_findings"]
        if latest.site_score is not None and previous.site_score is not None:
            site_delta = latest.site_score - previous.site_score

    # Run history with each run's delta against the next-older run.
    run_rows = []
    for index, run in enumerate(runs):
        older = runs[index + 1] if index + 1 < len(runs) else None
        delta = (
            run.site_score - older.site_score
            if older is not None and run.site_score is not None and older.site_score is not None
            else None
        )
        run_rows.append({"run": run, "delta": delta})

    return templates.TemplateResponse(
        request,
        "site_detail.html",
        {
            "site": site,
            "latest": latest,
            "audits": audits,
            "site_delta": site_delta,
            "changed_findings": changed_findings,
            "run_rows": run_rows,
        },
    )
