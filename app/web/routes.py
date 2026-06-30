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
from app.models import Account, AuditRun, Site
from app.models.enums import RunStatus
from app.reporting.view import build_action_list, build_audit_view, build_comparison
from app.runner import create_pending_run
from app.tenancy import get_current_account, owned_site
from app.worker_control import ensure_worker, worker_status

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


def _latest_run(session: Session, site_id: UUID) -> AuditRun | None:
    """The most recent run of any status (used by the live status fragment)."""
    return session.execute(
        select(AuditRun)
        .where(AuditRun.site_id == site_id)
        .order_by(AuditRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _latest_completed_run(session: Session, site_id: UUID) -> AuditRun | None:
    """The most recent completed run (used for score display on the dashboard)."""
    return session.execute(
        select(AuditRun)
        .where(AuditRun.site_id == site_id, AuditRun.status == RunStatus.complete)
        .order_by(AuditRun.started_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def _normalise_domain(raw: str) -> str:
    host = raw.strip().lower()
    host = host.split("://", 1)[-1]
    return host.split("/", 1)[0]


@router.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request,
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
) -> HTMLResponse:
    sites = (
        session.execute(
            select(Site)
            .where(Site.account_id == account.id)
            .order_by(Site.created_at.desc())
        )
        .scalars()
        .all()
    )
    rows = [{"site": site, "run": _latest_completed_run(session, site.id)} for site in sites]
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
    account: Account = Depends(get_current_account),
):
    host = _normalise_domain(domain)
    if not host:
        return templates.TemplateResponse(
            request, "site_form.html", {"site": None, "error": "Enter a valid domain."},
            status_code=400,
        )
    site = Site(
        account_id=account.id,
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
    site_id: UUID,
    request: Request,
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
) -> HTMLResponse:
    site = owned_site(session, site_id, account.id)
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
    account: Account = Depends(get_current_account),
):
    site = owned_site(session, site_id, account.id)
    if site is None:
        return HTMLResponse("Site not found", status_code=404)
    site.name = name or None
    site.business_type = business_type or None
    site.is_local = is_local
    site.is_multilingual = is_multilingual
    site.is_ymyl = is_ymyl
    session.commit()
    return RedirectResponse(url=f"/sites/{site.id}", status_code=303)


@router.post("/sites/{site_id}/run")
def run_site(
    site_id: UUID,
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
):
    site = owned_site(session, site_id, account.id)
    if site is None:
        return HTMLResponse("Site not found", status_code=404)
    create_pending_run(site.domain, account_id=account.id)  # queued
    ensure_worker()  # make sure something is there to execute it
    return RedirectResponse(url=f"/sites/{site_id}", status_code=303)


@router.get("/worker/status", response_class=HTMLResponse)
def worker_status_fragment(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_worker_status.html", {"worker": worker_status()}
    )


@router.post("/worker/start")
def start_worker(request: Request) -> HTMLResponse:
    ensure_worker()
    return templates.TemplateResponse(
        request, "_worker_status.html", {"worker": worker_status()}
    )


@router.get("/sites/{site_id}/status", response_class=HTMLResponse)
def site_status(
    site_id: UUID,
    request: Request,
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
) -> HTMLResponse:
    if owned_site(session, site_id, account.id) is None:
        return HTMLResponse("Site not found", status_code=404)
    run = _latest_run(session, site_id)
    return templates.TemplateResponse(
        request, "_run_status.html", {"site_id": site_id, "run": run}
    )


@router.get("/sites/{site_id}", response_class=HTMLResponse)
def site_detail(
    site_id: UUID,
    request: Request,
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
) -> HTMLResponse:
    site = owned_site(session, site_id, account.id)
    if site is None:
        return HTMLResponse("Site not found", status_code=404)

    # Newest first, by creation time so a queued run (no started_at yet) still orders
    # correctly and never trips date formatting.
    runs = session.execute(
        select(AuditRun)
        .where(AuditRun.site_id == site_id)
        .order_by(AuditRun.created_at.desc())
    ).scalars().all()

    # The results panels show the latest *completed* run; an in-progress run is
    # surfaced by the self-polling status fragment, not here.
    completed = [r for r in runs if r.status == RunStatus.complete]
    latest = completed[0] if completed else None
    previous = completed[1] if len(completed) > 1 else None
    audits = build_audit_view(session, latest) if latest is not None else []
    action_list = build_action_list(session, latest) if latest is not None else []

    site_delta = None
    changed_findings: list[dict] = []
    if latest is not None and previous is not None:
        changed_findings = build_comparison(session, audits, previous)["changed_findings"]
        if latest.site_score is not None and previous.site_score is not None:
            site_delta = latest.site_score - previous.site_score

    # Run history with each run's delta against the next-older run that has a score
    # (so a pending or failed run in between doesn't blank out the comparison).
    run_rows = []
    for index, run in enumerate(runs):
        older = next((r for r in runs[index + 1 :] if r.site_score is not None), None)
        delta = (
            run.site_score - older.site_score
            if older is not None and run.site_score is not None
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
            "action_list": action_list,
            "site_delta": site_delta,
            "changed_findings": changed_findings,
            "run_rows": run_rows,
        },
    )
