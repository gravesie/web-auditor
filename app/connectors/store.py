"""Persisting and reading a site's encrypted connector credentials.

A Connection row holds one source (Search Console or GA4) for one site. The
credentials blob is an encrypted JSON object: the OAuth refresh token plus the bound
resource id (the GSC property URL or the GA4 numeric property id).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Connection
from app.models.enums import ConnectionSource, ConnectionStatus
from app.security import crypto


@dataclass(frozen=True)
class Credentials:
    refresh_token: str
    resource_id: str  # GSC site URL, or GA4 numeric property id


def get_connection(
    session: Session, site_id: uuid.UUID, source_type: ConnectionSource
) -> Connection | None:
    return session.execute(
        select(Connection).where(
            Connection.site_id == site_id, Connection.source_type == source_type
        )
    ).scalar_one_or_none()


def save_connection(
    session: Session,
    site_id: uuid.UUID,
    source_type: ConnectionSource,
    *,
    refresh_token: str,
    resource_id: str,
) -> Connection:
    """Create or update the connection for (site, source), storing encrypted creds.

    Reconnecting an existing source overwrites its credentials and clears any error.
    """
    blob = crypto.encrypt_json({"refresh_token": refresh_token, "resource_id": resource_id})
    connection = get_connection(session, site_id, source_type)
    if connection is None:
        connection = Connection(site_id=site_id, source_type=source_type)
        session.add(connection)
    connection.credentials_encrypted = blob
    connection.status = ConnectionStatus.connected
    session.flush()
    return connection


def read_credentials(connection: Connection) -> Credentials:
    """Decrypt a connection's stored credentials. Raises on a missing/corrupt blob."""
    if not connection.credentials_encrypted:
        raise crypto.DecryptionError("connection has no stored credentials")
    data = crypto.decrypt_json(connection.credentials_encrypted)
    return Credentials(
        refresh_token=str(data["refresh_token"]),
        resource_id=str(data["resource_id"]),
    )
