"""FastAPI application entry point.

Serves the server-rendered dashboard and a health endpoint. Audits run in
background workers (added when the acquisition stage lands), not here.
"""

from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.db import engine, get_session
from app.models import Site

app = FastAPI(title="Web Auditor", version="0.1.0")

_TEMPLATES_DIR = Path(__file__).parent / "web" / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness plus a database reachability check."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        database = "ok"
    except Exception:  # noqa: BLE001 -- report unreachable rather than crash the probe
        database = "unreachable"
    return {"status": "ok", "env": settings.app_env, "database": database}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request, session: Session = Depends(get_session)) -> HTMLResponse:
    sites = session.execute(select(Site).order_by(Site.created_at.desc())).scalars().all()
    return templates.TemplateResponse(
        request, "dashboard.html", {"sites": sites}
    )
