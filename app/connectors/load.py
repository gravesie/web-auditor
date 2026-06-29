"""Assemble a site's live connector data for the audit run.

Called by the runner before the audits execute. Each source is fetched independently
and defensively: a failure marks that connection in error and is left out of the
returned dict, so the audits fall back to their public/inferred path. A connector
problem must never break an audit run.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import ga4, google_oauth, gsc, store
from app.models import Connection, Site
from app.models.enums import ConnectionSource, ConnectionStatus


def load_google_connectors(session: Session, site: Site) -> dict:
    """Return {"search_console": {...}, "ga4": {...}} for whatever is connected.

    Sources that are absent, disconnected, or error out are simply omitted.
    """
    connectors: dict = {}
    connections = session.execute(
        select(Connection).where(Connection.site_id == site.id)
    ).scalars().all()

    for connection in connections:
        if connection.status == ConnectionStatus.disconnected:
            continue
        if connection.source_type == ConnectionSource.search_console:
            data = _fetch(session, connection, _fetch_gsc)
            if data is not None:
                connectors["search_console"] = data
        elif connection.source_type == ConnectionSource.ga4:
            data = _fetch(session, connection, _fetch_ga4)
            if data is not None:
                connectors["ga4"] = data

    return connectors


def _fetch(session: Session, connection: Connection, fetcher) -> dict | None:
    """Run a source fetcher, recording success/failure on the connection row."""
    try:
        data = fetcher(connection)
    except Exception as exc:  # noqa: BLE001 -- degrade gracefully, never break the run
        connection.status = ConnectionStatus.error
        # Surface the cause for the dashboard without storing anything sensitive.
        connection.last_synced_at = datetime.now(UTC)
        session.flush()
        _log_connector_error(connection, exc)
        return None
    connection.status = ConnectionStatus.connected
    connection.last_synced_at = datetime.now(UTC)
    session.flush()
    return data


def _fetch_gsc(connection: Connection) -> dict:
    creds = store.read_credentials(connection)
    access_token = google_oauth.refresh_access_token(creds.refresh_token)
    summary = gsc.GscClient(access_token, creds.resource_id).fetch_summary()
    data = asdict(summary)
    data["site_url"] = creds.resource_id
    return data


def _fetch_ga4(connection: Connection) -> dict:
    creds = store.read_credentials(connection)
    access_token = google_oauth.refresh_access_token(creds.refresh_token)
    summary = ga4.Ga4Client(access_token, creds.resource_id).fetch_summary()
    data = asdict(summary)
    data["property_id"] = creds.resource_id
    return data


def _log_connector_error(connection: Connection, exc: Exception) -> None:
    # Lightweight stderr log; the connector layer has no logger of its own yet.
    print(f"[connectors] {connection.source_type} sync failed: {exc}")
