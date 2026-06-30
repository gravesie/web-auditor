"""Orchestrates a single audit run: acquire once, run the modules, persist a snapshot.

This is the synchronous core of the pipeline. The background-worker wrapper is
added later; the logic here is what the worker will call.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.acquisition.crawler import CrawledPage, crawl
from app.acquisition.fetcher import Acquisition, fetch
from app.acquisition.pagespeed import fetch_pagespeed
from app.acquisition.render import render
from app.audits.base import AuditContext, AuditModule, AuditResult
from app.audits.build_security import BuildSecurityAudit
from app.audits.compliance import ComplianceAudit
from app.audits.content_quality import ContentQualityAudit
from app.audits.geo import GeoAudit
from app.audits.messaging import MessagingAudit
from app.audits.on_page_seo import OnPageSeoAudit
from app.audits.performance import PerformanceAudit
from app.audits.schema import SchemaAudit
from app.audits.technical_seo import TechnicalSeoAudit
from app.config import settings
from app.connectors.load import load_google_connectors
from app.db import SessionLocal
from app.models import AuditRun, Finding, Page, Site, SubAuditResult
from app.models.enums import RunStatus, RunTrigger

# The audits to run. Grows as modules are added.
AUDIT_MODULES: list[AuditModule] = [
    BuildSecurityAudit(),
    ComplianceAudit(),
    TechnicalSeoAudit(),
    OnPageSeoAudit(),
    SchemaAudit(),
    ContentQualityAudit(),
    PerformanceAudit(),
    MessagingAudit(),
    GeoAudit(),
]


def _as_uuid(run_id: str | uuid.UUID) -> uuid.UUID:
    return run_id if isinstance(run_id, uuid.UUID) else uuid.UUID(run_id)


def _active_run_id(session: Session, site_id: uuid.UUID) -> str | None:
    """Id of the site's current pending-or-running run, or None if it has none."""
    run = session.execute(
        select(AuditRun)
        .where(
            AuditRun.site_id == site_id,
            AuditRun.status.in_([RunStatus.pending, RunStatus.running]),
        )
        .order_by(AuditRun.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    return str(run.id) if run is not None else None


def create_pending_run(domain: str, *, email: bool = False, scheduled: bool = False) -> str:
    """Create a pending run for a domain (creating the site if needed). Returns the run id.

    This is the enqueue step: the web request / scheduler calls it, then the worker
    picks the pending run up and executes it.

    Single-flight: a site may have only one active (pending or running) run at a
    time. If one already exists we reuse it instead of queueing a duplicate, so
    repeated button clicks and an overlapping scheduled run all collapse to the
    same run rather than auditing the site several times over. A partial unique
    index enforces this at the database level; the IntegrityError branch handles
    the race where two requests pass the check at once.
    """
    session = SessionLocal()
    try:
        site = session.query(Site).filter_by(domain=domain).one_or_none()
        if site is None:
            site = Site(domain=domain)
            session.add(site)
            session.flush()

        active = _active_run_id(session, site.id)
        if active is not None:
            return active

        run = AuditRun(
            site_id=site.id,
            status=RunStatus.pending,
            trigger=RunTrigger.scheduled if scheduled else RunTrigger.manual,
            email_requested=email,
        )
        session.add(run)
        try:
            session.commit()
        except IntegrityError:
            # A concurrent enqueue won the race; reuse the run it created.
            session.rollback()
            active = _active_run_id(session, site.id)
            if active is not None:
                return active
            raise
        return str(run.id)
    finally:
        session.close()


def reap_stuck_runs() -> int:
    """Fail any run still marked 'running', returning how many were reaped.

    Called by the worker at startup, *after* it has taken the single-instance lock:
    no other worker is alive, so a run still in 'running' was orphaned by a worker
    that died mid-audit. Failing it frees the single-flight slot and stops the
    dashboard showing a run that will never finish.
    """
    session = SessionLocal()
    try:
        stuck = (
            session.execute(select(AuditRun).where(AuditRun.status == RunStatus.running))
            .scalars()
            .all()
        )
        for run in stuck:
            run.status = RunStatus.failed
            run.error_message = "Worker stopped before this run finished."
            run.finished_at = datetime.now(UTC)
        session.commit()
        return len(stuck)
    finally:
        session.close()


def execute_run(run_id: str | uuid.UUID) -> dict:
    """Execute an existing run: acquire once, run every audit, persist the snapshot.

    Marks the run running, then complete; on failure marks it failed with the error.
    """
    session = SessionLocal()
    run = session.get(AuditRun, _as_uuid(run_id))
    if run is None:
        session.close()
        raise ValueError(f"audit run {run_id} not found")
    site = session.get(Site, run.site_id)
    domain = site.domain
    run.status = RunStatus.running
    run.started_at = datetime.now(UTC)
    run.error_message = None
    session.commit()

    try:
        acq = fetch(domain)

        crawled = crawl(domain)
        if not crawled:
            crawled = [CrawledPage(acq.final_url or acq.requested_url, 0, acq.status_code)]
        for cp in crawled:
            session.add(Page(run_id=run.id, url=cp.url, depth=cp.depth, status_code=cp.status))
        session.flush()

        rendered = render(acq.final_url or domain, run_axe=True)
        pagespeed = fetch_pagespeed(
            acq.final_url or domain, settings.pagespeed_api_key, strategy="mobile"
        )

        # Live connectors (Search Console, GA4) when the site has them. Failures here
        # degrade to the public path; they never break the run.
        connectors = load_google_connectors(session, site)
        session.commit()

        context = AuditContext(
            site_domain=domain,
            data={
                "acquisition": acq,
                "pages": crawled,
                "render": rendered,
                "pagespeed": pagespeed,
            },
            connectors=connectors,
        )

        results: list[tuple[AuditResult, SubAuditResult]] = []
        for module in AUDIT_MODULES:
            result = module.run(context)
            sar = SubAuditResult(
                run_id=run.id,
                audit_key=result.audit_key,
                score=result.score,
                completeness=result.completeness,
            )
            session.add(sar)
            session.flush()
            for category in result.categories:
                for check in category.checks:
                    session.add(
                        Finding(
                            sub_audit_result_id=sar.id,
                            category=category.key,
                            check_key=check.key,
                            status=check.status,
                            severity=check.severity,
                            detection_tag=check.detection,
                            value=check.value,
                            recommendation=check.recommendation,
                            evidence=check.evidence or None,
                        )
                    )
            results.append((result, sar))

        # Site score is the mean of the audits that produced a score; audits that
        # could not be assessed (score None) are excluded and the share rebalanced.
        scored = [r.score for r, _ in results if r.score is not None]
        run.site_score = sum(scored) / len(scored) if scored else None
        share = 1.0 / len(scored) if scored else 0.0
        for result, sar in results:
            sar.weighted_contribution = result.score * share if result.score is not None else None

        run.status = RunStatus.complete
        run.finished_at = datetime.now(UTC)
        session.commit()
        return _summary(domain, run, acq, results, len(crawled))
    except Exception as exc:
        # Roll back the partial snapshot, then record the failure on the run row.
        session.rollback()
        failed = session.get(AuditRun, _as_uuid(run_id))
        if failed is not None:
            failed.status = RunStatus.failed
            failed.error_message = str(exc)[:1000]
            failed.finished_at = datetime.now(UTC)
            session.commit()
        raise
    finally:
        session.close()


def run_audit(domain: str) -> dict:
    """Create and immediately execute a run, in-process. Used by the CLI."""
    return execute_run(create_pending_run(domain))


def _summary(
    domain: str,
    run: AuditRun,
    acq: Acquisition,
    results: list[tuple[AuditResult, SubAuditResult]],
    page_count: int,
) -> dict:
    return {
        "domain": domain,
        "run_id": str(run.id),
        "final_url": acq.final_url,
        "status_code": acq.status_code,
        "error": acq.error,
        "page_count": page_count,
        "site_score": run.site_score,
        "audits": [
            {
                "key": result.audit_key,
                "score": result.score,
                "completeness": result.completeness,
                "categories": [
                    {
                        "key": c.key,
                        "applicable": c.applicable,
                        "score": c.score,
                        "checks": [
                            {
                                "key": ch.key,
                                "status": str(ch.status),
                                "score": ch.score,
                                "detection": str(ch.detection),
                                "value": ch.value,
                            }
                            for ch in c.checks
                        ],
                    }
                    for c in result.categories
                ],
            }
            for result, _ in results
        ],
    }
