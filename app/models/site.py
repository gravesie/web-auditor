"""Site and its data-source connections."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.enums import ConnectionSource, ConnectionStatus


class Site(IdMixin, TimestampMixin, Base):
    """The primary unit of audit. Pages and queries hang beneath each run."""

    __tablename__ = "sites"

    domain: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(255))
    business_type: Mapped[str | None] = mapped_column(String(100))

    # Profile flags drive the conditional audit categories (international, local).
    is_local: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_multilingual: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    is_ymyl: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    connections: Mapped[list["Connection"]] = relationship(
        back_populates="site", cascade="all, delete-orphan"
    )
    runs: Mapped[list["AuditRun"]] = relationship(  # noqa: F821 (resolved via registry)
        back_populates="site", cascade="all, delete-orphan"
    )


class Connection(IdMixin, TimestampMixin, Base):
    """A configured data source for a site. Credentials are stored encrypted."""

    __tablename__ = "connections"

    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[ConnectionSource] = mapped_column(
        Enum(ConnectionSource, native_enum=False, length=50)
    )
    status: Mapped[ConnectionStatus] = mapped_column(
        Enum(ConnectionStatus, native_enum=False, length=30),
        default=ConnectionStatus.connected,
    )
    credentials_encrypted: Mapped[str | None] = mapped_column(Text)
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    site: Mapped["Site"] = relationship(back_populates="connections")
