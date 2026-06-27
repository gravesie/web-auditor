"""Orchestrates a single audit run: acquire once, run the modules, persist a snapshot.

This is the synchronous core of the pipeline. The background-worker wrapper is
added later; the logic here is what the worker will call.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.acquisition.fetcher import Acquisition, fetch
from app.audits.base import AuditContext, AuditModule, AuditResult
from app.audits.build_security import BuildSecurityAudit
from app.db import SessionLocal
from app.models import AuditRun, Finding, Page, Site, SubAuditResult
from app.models.enums import RunStatus

# The audits to run. Grows as modules are added.
AUDIT_MODULES: list[AuditModule] = [BuildSecurityAudit()]


def run_audit(domain: str) -> dict:
    """Run every registered audit against a domain and store the result."""
    session = SessionLocal()
    try:
        site = session.query(Site).filter_by(domain=domain).one_or_none()
        if site is None:
            site = Site(domain=domain)
            session.add(site)
            session.flush()

        run = AuditRun(site_id=site.id, status=RunStatus.running, started_at=datetime.now(UTC))
        session.add(run)
        session.flush()

        acq = fetch(domain)
        page = Page(run_id=run.id, url=acq.final_url or acq.requested_url)
        session.add(page)
        session.flush()

        context = AuditContext(site_domain=domain, data={"acquisition": acq})

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

        # Site score is the mean of the audits that ran (one for now). Each audit's
        # weighted contribution uses an equal share among the audits in this run.
        run_scores = [r.score for r, _ in results]
        run.site_score = sum(run_scores) / len(run_scores) if run_scores else None
        share = 1.0 / len(results) if results else 0.0
        for result, sar in results:
            sar.weighted_contribution = result.score * share

        run.status = RunStatus.complete
        run.finished_at = datetime.now(UTC)
        session.commit()

        return _summary(domain, run, acq, results)
    except Exception:
        # The acquisition step never raises, so a failure here is a storage problem.
        # Roll back the whole run rather than leave a half-written snapshot.
        session.rollback()
        raise
    finally:
        session.close()


def _summary(
    domain: str,
    run: AuditRun,
    acq: Acquisition,
    results: list[tuple[AuditResult, SubAuditResult]],
) -> dict:
    return {
        "domain": domain,
        "final_url": acq.final_url,
        "status_code": acq.status_code,
        "error": acq.error,
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
