"""Shared run-view builder.

Turns an audit run into the structure the dashboard and the PDF report both render:
the site, the run, and each audit with its findings grouped by category (worst
first). Keeping this in one place means the report and the dashboard never drift.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditRun, Finding, SubAuditResult
from app.runner import AUDIT_MODULES

# Human labels for audits and their categories, from the registered modules.
AUDIT_LABELS = {m.key: m.label for m in AUDIT_MODULES}
CATEGORY_LABELS = {m.key: {c.key: c.label for c in m.categories} for m in AUDIT_MODULES}

# Surface the worst findings first within a category.
_STATUS_ORDER = {"fail": 0, "warn": 1, "pass": 2, "info": 3}


def build_audit_view(session: Session, run: AuditRun) -> list[dict]:
    """The per-audit breakdown for a run: scores, completeness and grouped findings."""
    results = session.execute(
        select(SubAuditResult)
        .where(SubAuditResult.run_id == run.id)
        .order_by(SubAuditResult.audit_key)
    ).scalars().all()

    audits = []
    for sar in results:
        findings = session.execute(
            select(Finding).where(Finding.sub_audit_result_id == sar.id)
        ).scalars().all()
        grouped: dict[str, list[Finding]] = {}
        for finding in findings:
            grouped.setdefault(finding.category, []).append(finding)
        labels = CATEGORY_LABELS.get(sar.audit_key, {})
        categories = [
            {
                "key": key,
                "label": labels.get(key, key),
                "findings": sorted(items, key=lambda f: _STATUS_ORDER.get(str(f.status), 9)),
            }
            for key, items in grouped.items()
        ]
        audits.append(
            {
                "key": sar.audit_key,
                "label": AUDIT_LABELS.get(sar.audit_key, sar.audit_key),
                "score": sar.score,
                "completeness": sar.completeness,
                "weighted": sar.weighted_contribution,
                "categories": categories,
            }
        )
    return audits
