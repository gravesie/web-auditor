"""Server-rendered dashboard views: site list and run drill-down."""

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
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


def _normalise_domain(raw: str) -> str:
    host = raw.strip().lower()
    host = host.split("://", 1)[-1]
    return host.split("/", 1)[0]


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    sites = session.execute(select(Site).order_by(Site.created_at.desc())).scalars().all()
    rows = [{"site": site, "run": _latest_run(session, site.id)} for site in sites]
    return templates.TemplateResponse(request, "dashboard.html", {"rows": rows})


# Static /sites paths must be declared before the dynamic /sites/{site_id} route,
# or "new" would be matched as a (non-UUID) site id and 422.
@router.get("/sites/new", response_class=HTMLResponse)
def new_site_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "site_form.html", {"site": None, "error": None})


@router.post("/sites")
def create_site(
    request: Request,
    domain: str = Form(...),
    name: str = Form(""),
    business_type: str = Form(""),
    is_local: bool = Form(False),
    is_multilingual: bool = Form(False),
    is_ymyl: bool = Form(False),
    session: Session = Depends(get_session),
):
    host = _normalise_domain(domain)
    if not host:
        return templates.TemplateResponse(
            request, "site_form.html", {"site": None, "error": "Enter a valid domain."},
            status_code=400,
        )
    site = Site(
        domain=host,
        name=name or None,
        business_type=business_type or None,
        is_local=is_local,
        is_multilingual=is_multilingual,
        is_ymyl=is_ymyl,
    )
    session.add(site)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return templates.TemplateResponse(
            request, "site_form.html",
            {"site": None, "error": f"A site with domain '{host}' already exists."},
            status_code=409,
        )
    return RedirectResponse(url=f"/sites/{site.id}", status_code=303)


@router.get("/sites/{site_id}/edit", response_class=HTMLResponse)
def edit_site_form(
    site_id: UUID, request: Request, session: Session = Depends(get_session)
) -> HTMLResponse:
    site = session.get(Site, site_id)
    if site is None:
        return HTMLResponse("Site not found", status_code=404)
    return templates.TemplateResponse(request, "site_form.html", {"site": site, "error": None})


@router.post("/sites/{site_id}/edit")
def update_site(
    site_id: UUID,
    request: Request,
    name: str = Form(""),
    business_type: str = Form(""),
    is_local: bool = Form(False),
    is_multilingual: bool = Form(False),
    is_ymyl: bool = Form(False),
    session: Session = Depends(get_session),
):
    site = session.get(Site, site_id)
    if site is None:
        return HTMLResponse("Site not found", status_code=404)
    site.name = name or None
    site.business_type = business_type or None
    site.is_local = is_local
    site.is_multilingual = is_multilingual
    site.is_ymyl = is_ymyl
    session.commit()
    return RedirectResponse(url=f"/sites/{site.id}", status_code=303)


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
