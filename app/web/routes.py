"""Server-rendered dashboard views: site list and run drill-down."""

import base64
import html
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from app import __version__
from app.acquisition.screenshot import capture_screenshot
from app.auth import (
    ADMIN_ROLES,
    clear_session,
    current_user,
    login_session,
    require_admin,
    require_user,
)
from app.config import settings
from app.db import get_session
from app.magic import send_magic_link, verify_magic_token
from app.models import Account, AuditRun, Site, User
from app.models.enums import RunStatus
from app.onboarding import (
    FREE_PLAN,
    MAX_VISITOR_AUDITS,
    normalize_domain,
    valid_domain,
    valid_email,
)
from app.plans import at_site_limit, max_sites_for
from app.reporting.presentation import build_page_one
from app.reporting.view import build_action_list, build_audit_view, build_comparison
from app.runner import create_pending_run
from app.security.passwords import verify_password
from app.tenancy import get_current_account, owned_site
from app.worker_control import ensure_worker, worker_status

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Templates load once at startup, matching the code loaded at startup. Without this,
# Jinja auto-reloads templates from disk while the Python code stays as it was, so a
# not-yet-restarted server can render a new template against an old route and 500.
# Deploys are a restart anyway (via the admin Restart button), which reloads both.
templates.env.auto_reload = False

# Repo root, for the admin "refresh code" action (git pull).
_REPO_ROOT = Path(__file__).resolve().parents[2]
# The dev server binds here; the restart action re-execs uvicorn with these.
_SERVER_HOST = "127.0.0.1"
_SERVER_PORT = "8000"


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


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, user: User | None = Depends(current_user)) -> HTMLResponse:
    if user is not None:
        return RedirectResponse("/", status_code=303)
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login")
def login_submit(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session),
):
    user = session.execute(
        select(User).where(User.email == email.strip().lower())
    ).scalar_one_or_none()
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Wrong email or password."}, status_code=401
        )
    login_session(request, user)
    return RedirectResponse("/", status_code=303)


@router.get("/logout")
def logout(request: Request) -> RedirectResponse:
    clear_session(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/magic/{token}", response_class=HTMLResponse)
def magic_login(
    token: str, request: Request, session: Session = Depends(get_session)
) -> Response:
    """Sign a customer in from an emailed magic link."""
    user_id = verify_magic_token(token)
    user = session.get(User, UUID(user_id)) if user_id else None
    if user is None or not user.is_active:
        return templates.TemplateResponse(
            request, "magic_invalid.html", {}, status_code=400
        )
    login_session(request, user)
    return RedirectResponse("/", status_code=303)


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    session: Session = Depends(get_session),
    user: User | None = Depends(current_user),
) -> HTMLResponse:
    """The front door: the public landing for visitors, the dashboard once signed in."""
    if user is None:
        return templates.TemplateResponse(
            request, "landing.html", {"cta": settings.landing_cta, "error": None}
        )
    account = session.get(Account, user.account_id)
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
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "rows": rows,
            "account": account,
            "site_limit": max_sites_for(account.plan_tier),
            "at_limit": at_site_limit(len(sites), account.plan_tier),
        },
    )


@router.post("/audit", response_class=HTMLResponse)
def start_audit(
    request: Request, url: str = Form(...), session: Session = Depends(get_session)
) -> HTMLResponse:
    """Public: validate a URL, start an audit, and ask for the visitor's email."""
    if not valid_domain(url):
        return templates.TemplateResponse(
            request,
            "landing.html",
            {"cta": settings.landing_cta, "error": "Please enter a valid website address."},
            status_code=400,
        )
    ob = request.session.get("ob") or {"account_id": None, "count": 0, "email": False}

    if ob["count"] >= MAX_VISITOR_AUDITS:
        return templates.TemplateResponse(
            request, "audit_limit.html", {"max": MAX_VISITOR_AUDITS}
        )
    if ob["count"] >= 1 and not ob["email"]:
        # No second audit until we have the email for the first.
        return templates.TemplateResponse(
            request, "audit_running.html", {"await_email": True, "email_error": None}
        )

    domain = normalize_domain(url)
    account = session.get(Account, UUID(ob["account_id"])) if ob["account_id"] else None
    if account is None:
        account = Account(name=f"Visitor: {domain}", plan_tier=FREE_PLAN)
        session.add(account)
        session.commit()
    ob["account_id"] = str(account.id)

    create_pending_run(domain, account_id=account.id, email=False)
    ensure_worker()
    shot = capture_screenshot(domain)
    ob["count"] += 1
    request.session["ob"] = ob

    return templates.TemplateResponse(
        request,
        "audit_running.html",
        {
            "await_email": False,
            "domain": domain,
            "screenshot": base64.b64encode(shot).decode("ascii") if shot else None,
            "email_captured": ob["email"],
            "count": ob["count"],
            "max": MAX_VISITOR_AUDITS,
            "email_error": None,
        },
    )


@router.post("/audit/email", response_class=HTMLResponse)
def capture_email(
    request: Request, email: str = Form(...), session: Session = Depends(get_session)
) -> Response:
    """Public: capture the visitor's email and send them a magic link to their results."""
    ob = request.session.get("ob") or {}
    if not ob.get("account_id"):
        return RedirectResponse("/", status_code=303)
    if not valid_email(email):
        return templates.TemplateResponse(
            request,
            "audit_running.html",
            {"await_email": True, "email_error": "Please enter a valid email address."},
            status_code=400,
        )
    addr = email.strip().lower()
    account_id = UUID(ob["account_id"])
    user = session.execute(select(User).where(User.email == addr)).scalar_one_or_none()
    if user is None:
        user = User(account_id=account_id, email=addr, role="member")
        session.add(user)
        session.commit()
    ob["email"] = True
    request.session["ob"] = ob
    send_magic_link(user)
    return templates.TemplateResponse(
        request,
        "audit_running.html",
        {"await_email": False, "email_captured": True, "email_sent_to": addr, "email_error": None},
    )


_ADMIN_SORTS = {"site", "customer", "score", "last_run"}
_PER_PAGE_OPTIONS = [25, 50, 100, 200]


def _sort_key(sort: str):
    if sort == "site":
        return lambda r: (r["site"].name or r["site"].domain).lower()
    if sort == "customer":
        return lambda r: r["customer"].lower()
    if sort == "score":
        return lambda r: (
            r["run"].site_score if r["run"] and r["run"].site_score is not None else -1
        )
    return lambda r: (r["run"].started_at.timestamp() if r["run"] and r["run"].started_at else 0)


@router.get("/admin", response_class=HTMLResponse)
def admin_dashboard(
    request: Request,
    _: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """Every customer's sites, sortable and paginated. Goyande staff only."""
    params = request.query_params
    sort = params.get("sort") if params.get("sort") in _ADMIN_SORTS else "last_run"
    direction = params.get("dir") if params.get("dir") in ("asc", "desc") else "desc"
    try:
        per_page = int(params.get("per_page", 25))
    except ValueError:
        per_page = 25
    if per_page not in _PER_PAGE_OPTIONS:
        per_page = 25
    try:
        page = max(1, int(params.get("page", 1)))
    except ValueError:
        page = 1

    sites = session.execute(select(Site)).scalars().all()
    names = {a.id: a.name for a in session.execute(select(Account)).scalars().all()}
    rows = [
        {
            "site": site,
            "run": _latest_completed_run(session, site.id),
            "customer": names.get(site.account_id, "—"),
        }
        for site in sites
    ]
    rows.sort(key=_sort_key(sort), reverse=(direction == "desc"))

    total = len(rows)
    pages = max(1, -(-total // per_page))  # ceil
    page = min(page, pages)
    start = (page - 1) * per_page
    return templates.TemplateResponse(
        request,
        "admin_dashboard.html",
        {
            "rows": rows[start : start + per_page],
            "customer_count": len(names),
            "total": total,
            "sort": sort,
            "dir": direction,
            "per_page": per_page,
            "per_page_options": _PER_PAGE_OPTIONS,
            "page": page,
            "pages": pages,
            "version": __version__,
        },
    )


@router.get("/admin/users", response_class=HTMLResponse)
def admin_users(
    request: Request,
    current: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> HTMLResponse:
    """All registered users and their access level. Goyande staff only."""
    users = session.execute(select(User).order_by(User.created_at)).scalars().all()
    names = {a.id: a.name for a in session.execute(select(Account)).scalars().all()}
    rows = [
        {
            "user": user,
            "account": names.get(user.account_id, "—"),
            "is_admin": user.role in ADMIN_ROLES,
            "is_master": user.role == "owner",
            "is_self": user.id == current.id,
        }
        for user in users
    ]
    return templates.TemplateResponse(
        request, "admin_users.html", {"rows": rows, "version": __version__}
    )


@router.post("/admin/users/{user_id}/role")
def set_user_role(
    user_id: UUID,
    action: str = Form(...),
    current: User = Depends(require_admin),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Promote a customer to admin, or demote an admin to customer."""
    target = session.get(User, user_id)
    if target is not None:
        if action == "promote":
            target.role = "admin"
        elif action == "demote" and target.id != current.id and target.role != "owner":
            # Never demote yourself or the master (owner) account.
            target.role = "member"
        session.commit()
    return RedirectResponse("/admin/users", status_code=303)


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
    site_count = session.execute(
        select(func.count()).select_from(Site).where(Site.account_id == account.id)
    ).scalar_one()
    if at_site_limit(site_count, account.plan_tier):
        limit = max_sites_for(account.plan_tier)
        return templates.TemplateResponse(
            request,
            "site_form.html",
            {
                "site": None,
                "error": f"You've reached your plan's limit of {limit} sites. "
                "Remove a site or upgrade to add more.",
            },
            status_code=403,
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


@router.post("/sites/{site_id}/delete")
def delete_site(
    site_id: UUID,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
) -> RedirectResponse:
    """Delete a site and its runs. Admins may delete any; others only their own."""
    site = session.get(Site, site_id)
    is_admin = user.role in ADMIN_ROLES
    if site is None or (not is_admin and site.account_id != user.account_id):
        return RedirectResponse("/admin" if is_admin else "/", status_code=303)
    session.delete(site)
    session.commit()
    return RedirectResponse("/admin" if is_admin else "/", status_code=303)


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
    page_one = build_page_one(session, latest) if latest is not None else None

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
            "page_one": page_one,
            "site_delta": site_delta,
            "changed_findings": changed_findings,
            "run_rows": run_rows,
        },
    )


@router.get("/sites/{site_id}/report")
def download_report(
    site_id: UUID,
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
) -> Response:
    """Download the branded PDF for the site's latest completed audit."""
    site = owned_site(session, site_id, account.id)
    if site is None:
        return Response("Site not found", status_code=404, media_type="text/plain")
    latest = _latest_completed_run(session, site_id)
    if latest is None:
        return Response(
            "No completed audit to download yet", status_code=404, media_type="text/plain"
        )

    # Imported here so the dashboard process only loads Playwright when a report is
    # actually requested, not at startup.
    from app.reporting.report import generate_report

    report = generate_report(latest.id)
    return Response(
        content=report.pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{report.filename}"'},
    )


# --- Admin controls: manage the running server without the terminal ---
# These run local process/git operations, so they must sit behind admin auth once the
# login wall exists. For now the app is localhost-only and single-account.


@router.post("/admin/refresh", response_class=HTMLResponse)
def admin_refresh(_: User = Depends(require_admin)) -> HTMLResponse:
    """Pull the latest code (git pull). A restart is still needed to load it."""
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only"],
            cwd=_REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = (result.stdout + result.stderr).strip() or "(no output)"
        ok = result.returncode == 0
    except (subprocess.SubprocessError, OSError) as exc:
        output, ok = f"git pull failed: {exc}", False
    note = (
        "Code updated. Click Restart server to load it."
        if ok
        else "Refresh failed, see below."
    )
    return HTMLResponse(f"<strong>{html.escape(note)}</strong>\n{html.escape(output)}")


@router.post("/admin/restart", response_class=HTMLResponse)
def admin_restart(_: User = Depends(require_admin)) -> HTMLResponse:
    """Restart the server by re-executing uvicorn in place.

    The response is returned first; a background thread then re-execs the process, so
    the browser gets the reconnecting panel before the socket drops. The panel polls
    /health and reloads the page once the new process is serving.
    """

    def _reexec() -> None:
        time.sleep(0.8)
        os.execv(
            sys.executable,
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", _SERVER_HOST, "--port", _SERVER_PORT],
        )

    threading.Thread(target=_reexec, daemon=True).start()
    return HTMLResponse(
        '<div hx-get="/health" hx-trigger="every 2s" hx-swap="none" '
        "hx-on::after-request=\"if(event.detail.successful){window.location.reload()}\">"
        "Restarting the server… this will reload automatically when it is back "
        "(a few seconds)."
        "</div>"
    )
