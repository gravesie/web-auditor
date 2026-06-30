"""FastAPI application entry point.

Serves the server-rendered dashboard (app/web/routes.py) and a health endpoint.
Audits run via the CLI / background workers, not in a web request.
"""

from fastapi import FastAPI
from sqlalchemy import text

from app.config import settings
from app.db import engine
from app.web.connections import router as connections_router
from app.web.routes import router as web_router

app = FastAPI(title="Web Auditor", version="0.1.0")


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


app.include_router(web_router)
app.include_router(connections_router)
