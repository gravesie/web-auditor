"""Web routes for connecting a site's Google data sources (Search Console + GA4).

The flow: from a site's connections page the user starts the Google consent; Google
redirects back to the callback with a code; we exchange it for a refresh token, list
the GSC properties and GA4 properties the grant can read, and let the user bind one
of each to the site. The refresh token is carried between the callback and the bind
step inside an encrypted blob (no server-side session needed) and only persisted,
encrypted, once a property is chosen.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session
from starlette.templating import Jinja2Templates

from app.connectors import ga4, google_oauth, gsc, store
from app.db import get_session
from app.models import Account, Connection
from app.models.enums import ConnectionSource, ConnectionStatus
from app.security import crypto
from app.tenancy import get_current_account, owned_site

router = APIRouter()

_TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
# Load templates once at startup, matching the loaded code (see routes.py note).
templates.env.auto_reload = False


def _connections_for(session: Session, site_id: UUID) -> dict[str, Connection]:
    rows = session.query(Connection).filter_by(site_id=site_id).all()
    return {str(c.source_type): c for c in rows}


@router.get("/sites/{site_id}/connections", response_class=HTMLResponse)
def connections_page(
    site_id: UUID,
    request: Request,
    error: str | None = None,
    connected: str | None = None,
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
) -> HTMLResponse:
    site = owned_site(session, site_id, account.id)
    if site is None:
        return HTMLResponse("Site not found", status_code=404)
    return templates.TemplateResponse(
        request,
        "connections.html",
        {
            "site": site,
            "connections": _connections_for(session, site_id),
            "oauth_configured": google_oauth.is_configured(),
            "error": error,
            "connected": connected,
        },
    )


@router.get("/sites/{site_id}/connections/google/start")
def google_start(
    site_id: UUID,
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
):
    site = owned_site(session, site_id, account.id)
    if site is None:
        return HTMLResponse("Site not found", status_code=404)
    if not google_oauth.is_configured():
        return RedirectResponse(
            url=f"/sites/{site_id}/connections?error=Google+OAuth+is+not+configured",
            status_code=303,
        )
    state = google_oauth.encode_state(str(site_id))
    return RedirectResponse(url=google_oauth.authorization_url(state), status_code=303)


@router.get("/connections/google/callback", response_class=HTMLResponse)
def google_callback(
    request: Request,
    state: str = "",
    code: str = "",
    error: str = "",
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
) -> HTMLResponse:
    # The user can deny consent; Google sends ?error=access_denied in that case.
    try:
        site_id = google_oauth.decode_state(state)
    except google_oauth.OAuthError:
        return HTMLResponse("Invalid or expired authorisation state.", status_code=400)

    redirect = f"/sites/{site_id}/connections"
    if error:
        return RedirectResponse(url=f"{redirect}?error=Consent+was+not+granted", status_code=303)
    if not code:
        return RedirectResponse(url=f"{redirect}?error=No+authorisation+code", status_code=303)

    site = owned_site(session, UUID(site_id), account.id)
    if site is None:
        return HTMLResponse("Site not found", status_code=404)

    try:
        grant = google_oauth.exchange_code(code)
    except google_oauth.OAuthError as exc:
        return RedirectResponse(url=f"{redirect}?error={_q(exc)}", status_code=303)

    if not grant.refresh_token:
        # Google only returns a refresh token with consent; without one we can't store
        # a durable grant. Ask the user to revoke and retry.
        return RedirectResponse(
            url=f"{redirect}?error=No+refresh+token+returned;+remove+the+app+at+"
            "myaccount.google.com/permissions+and+try+again",
            status_code=303,
        )

    # List what this grant can read so the user can bind one property of each.
    try:
        gsc_sites = gsc.list_sites(grant.access_token)
    except gsc.GscError:
        gsc_sites = []
    try:
        ga4_properties = ga4.list_properties(grant.access_token)
    except ga4.Ga4Error:
        ga4_properties = []

    # Carry the refresh token to the bind step encrypted, not in the clear.
    grant_blob = crypto.encrypt_json({"refresh_token": grant.refresh_token, "site_id": site_id})

    return templates.TemplateResponse(
        request,
        "connection_select.html",
        {
            "site": site,
            "gsc_sites": gsc_sites,
            "ga4_properties": ga4_properties,
            "grant_blob": grant_blob,
        },
    )


@router.post("/connections/google/bind")
def google_bind(
    grant: str = Form(...),
    gsc_site: str = Form(""),
    ga4_property: str = Form(""),
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
):
    try:
        payload = crypto.decrypt_json(grant)
    except crypto.DecryptionError:
        return HTMLResponse("Invalid grant.", status_code=400)

    site_id = UUID(str(payload["site_id"]))
    refresh_token = str(payload["refresh_token"])
    site = owned_site(session, site_id, account.id)
    if site is None:
        return HTMLResponse("Site not found", status_code=404)

    bound = 0
    if gsc_site:
        store.save_connection(
            session, site_id, ConnectionSource.search_console,
            refresh_token=refresh_token, resource_id=gsc_site,
        )
        bound += 1
    if ga4_property:
        store.save_connection(
            session, site_id, ConnectionSource.ga4,
            refresh_token=refresh_token, resource_id=ga4_property,
        )
        bound += 1

    session.commit()
    if bound == 0:
        return RedirectResponse(
            url=f"/sites/{site_id}/connections?error=Select+at+least+one+property",
            status_code=303,
        )
    return RedirectResponse(
        url=f"/sites/{site_id}/connections?connected={bound}+source(s)", status_code=303
    )


@router.post("/sites/{site_id}/connections/{source}/disconnect")
def disconnect(
    site_id: UUID,
    source: str,
    session: Session = Depends(get_session),
    account: Account = Depends(get_current_account),
):
    if owned_site(session, site_id, account.id) is None:
        return HTMLResponse("Site not found", status_code=404)
    try:
        source_type = ConnectionSource(source)
    except ValueError:
        return HTMLResponse("Unknown source", status_code=400)
    connection = store.get_connection(session, site_id, source_type)
    if connection is not None:
        connection.status = ConnectionStatus.disconnected
        session.commit()
    return RedirectResponse(url=f"/sites/{site_id}/connections", status_code=303)


def _q(value: object) -> str:
    """Make an error message safe to drop into a redirect query string."""
    return str(value).replace(" ", "+").replace("&", "and")[:200]
