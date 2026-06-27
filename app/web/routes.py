"""Server-rendered dashboard views: site list and run drill-down."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from app.db import get_session
from app.models import AuditRun, Finding, Site, SubAuditResult
from app.runner import AUDIT_MODULES

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

# Human labels for audits and their categories, taken from the registered modules.
AUDIT_LABELS = {m.key: m.label for m in AUDIT_MODULES}
CATEGORY_LABELS = {m.key: {c.key: c.label for c in m.categories} for m in AUDIT_MODULES}

# Surface the worst findings first within a category.
_STATUS_ORDER = {"fail": 0, "warn": 1, "pass": 2, "info": 3}


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

    audits = []
    if latest is not None:
        results = session.execute(
            select(SubAuditResult)
            .where(SubAuditResult.run_id == latest.id)
            .order_by(SubAuditResult.audit_key)
        ).scalars().all()
        for sar in results:
            findings = session.execute(
                select(Finding).where(Finding.sub_audit_result_id == sar.id)
            ).scalars().all()
            grouped: dict[str, list[Finding]] = {}
            for finding in findings:
                grouped.setdefault(finding.category, []).append(finding)
            labels = CATEGORY_LABELS.get(sar.audit_key, {})
            categories = [
                {
                    "key": key,
                    "label": labels.get(key, key),
                    "findings": sorted(items, key=lambda f: _STATUS_ORDER.get(str(f.status), 9)),
                }
                for key, items in grouped.items()
            ]
            audits.append(
                {
                    "key": sar.audit_key,
                    "label": AUDIT_LABELS.get(sar.audit_key, sar.audit_key),
                    "score": sar.score,
                    "completeness": sar.completeness,
                    "weighted": sar.weighted_contribution,
                    "categories": categories,
                }
            )

    return templates.TemplateResponse(
        request,
        "site_detail.html",
        {"site": site, "latest": latest, "runs": runs, "audits": audits},
    )
