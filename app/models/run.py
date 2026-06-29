"""Audit runs and their results.

Each run is a timestamped snapshot. We persist the site score, every sub-audit
result, the findings beneath them, and the page / query rows for the lower two
levels of the hierarchy. Deltas are computed by comparing runs, not stored.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin
from app.models.enums import (
    DetectionTag,
    FindingStatus,
    RunStatus,
    RunTrigger,
    Severity,
)


class AuditRun(IdMixin, TimestampMixin, Base):
    __tablename__ = "audit_runs"

    # A site may have at most one active run at a time. Enforced with a partial
    # unique index so concurrent enqueues cannot create duplicate active runs.
    __table_args__ = (
        Index(
            "uq_audit_runs_active_per_site",
            "site_id",
            unique=True,
            postgresql_where=text("status IN ('pending', 'running')"),
        ),
    )

    site_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sites.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, native_enum=False, length=20), default=RunStatus.pending
    )
    trigger: Mapped[RunTrigger] = mapped_column(
        Enum(RunTrigger, native_enum=False, length=20), default=RunTrigger.manual
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    site_score: Mapped[float | None] = mapped_column(Float)
    # The worker emails the PDF report on completion when this is set.
    email_requested: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    error_message: Mapped[str | None] = mapped_column(Text)

    site: Mapped["Site"] = relationship(back_populates="runs")  # noqa: F821
    results: Mapped[list["SubAuditResult"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    pages: Mapped[list["Page"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    queries: Mapped[list["Query"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class SubAuditResult(IdMixin, TimestampMixin, Base):
    __tablename__ = "sub_audit_results"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True
    )
    audit_key: Mapped[str] = mapped_column(String(50), index=True)
    score: Mapped[float | None] = mapped_column(Float)
    weighted_contribution: Mapped[float | None] = mapped_column(Float)
    # 0..1 share of this audit backed by observed / connected data vs inference.
    completeness: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)

    run: Mapped["AuditRun"] = relationship(back_populates="results")
    findings: Mapped[list["Finding"]] = relationship(
        back_populates="result", cascade="all, delete-orphan"
    )


class Page(IdMixin, TimestampMixin, Base):
    __tablename__ = "pages"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True
    )
    url: Mapped[str] = mapped_column(Text)
    depth: Mapped[int | None] = mapped_column(Integer)  # click depth from the homepage
    status_code: Mapped[int | None] = mapped_column(Integer)

    run: Mapped["AuditRun"] = relationship(back_populates="pages")
    queries: Mapped[list["Query"]] = relationship(back_populates="page")


class Query(IdMixin, TimestampMixin, Base):
    __tablename__ = "queries"

    run_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("audit_runs.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), index=True
    )
    phrase: Mapped[str] = mapped_column(Text)
    position: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(50))

    run: Mapped["AuditRun"] = relationship(back_populates="queries")
    page: Mapped["Page | None"] = relationship(back_populates="queries")


class Finding(IdMixin, TimestampMixin, Base):
    __tablename__ = "findings"

    sub_audit_result_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sub_audit_results.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(80), index=True)
    check_key: Mapped[str] = mapped_column(String(120), index=True)
    status: Mapped[FindingStatus] = mapped_column(
        Enum(FindingStatus, native_enum=False, length=10)
    )
    severity: Mapped[Severity] = mapped_column(Enum(Severity, native_enum=False, length=10))
    detection_tag: Mapped[DetectionTag] = mapped_column(
        Enum(DetectionTag, native_enum=False, length=20)
    )
    value: Mapped[str | None] = mapped_column(Text)
    recommendation: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSONB)

    # A finding may attach at site level (both null), page level, or query level.
    page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("pages.id", ondelete="SET NULL"))
    query_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("queries.id", ondelete="SET NULL")
    )

    result: Mapped["SubAuditResult"] = relationship(back_populates="findings")
