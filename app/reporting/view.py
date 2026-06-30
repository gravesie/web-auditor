"""Shared run-view builder.

Turns an audit run into the structure the dashboard and the PDF report both render:
the site, the run, and each audit with its findings grouped by category (worst
first). Keeping this in one place means the report and the dashboard never drift.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AuditRun, Finding, SubAuditResult
from app.remediation.ranking import ActionItem, rank_findings
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
                # Default; compare_audits() overwrites this when a previous run exists.
                # Without it, a site's first run has no delta key and the template that
                # renders delta_badge(audit.delta) raises.
                "delta": None,
            }
        )
    return audits


def build_action_list(session: Session, run: AuditRun) -> list[ActionItem]:
    """The prioritised action list for a run: actionable findings, impact-first.

    This is the output layer the dashboard and report lead with. The order is
    deterministic, set entirely by the remediation catalogue (see ranking.py).
    """
    rows = session.execute(
        select(Finding, SubAuditResult.audit_key)
        .join(SubAuditResult, Finding.sub_audit_result_id == SubAuditResult.id)
        .where(SubAuditResult.run_id == run.id)
    ).all()
    triples = [
        (finding, audit_key, AUDIT_LABELS.get(audit_key, audit_key))
        for finding, audit_key in rows
    ]
    return rank_findings(triples)


def _run_index(session: Session, run: AuditRun) -> tuple[dict, dict]:
    """For a run, map audit_key -> score and (audit, category, check) -> status string."""
    scores: dict[str, float | None] = {}
    statuses: dict[tuple[str, str, str], str] = {}
    results = session.execute(
        select(SubAuditResult).where(SubAuditResult.run_id == run.id)
    ).scalars().all()
    for sar in results:
        scores[sar.audit_key] = sar.score
        findings = session.execute(
            select(Finding).where(Finding.sub_audit_result_id == sar.id)
        ).scalars().all()
        for finding in findings:
            statuses[(sar.audit_key, finding.category, finding.check_key)] = str(finding.status)
    return scores, statuses


_STATUS_RANK = {"fail": 0, "warn": 1, "pass": 2, "info": 3}


def compare_audits(audits: list[dict], prev_scores: dict, prev_statuses: dict) -> dict:
    """Pure comparison: annotate each audit with a score 'delta' and collect status changes.

    prev_scores maps audit_key -> score; prev_statuses maps (audit, category, check) ->
    status string. Regressions (moving toward fail) sort first in changed_findings.
    """
    changed: list[dict] = []
    for audit in audits:
        prev = prev_scores.get(audit["key"])
        current = audit["score"]
        audit["delta"] = current - prev if (prev is not None and current is not None) else None
        for category in audit["categories"]:
            for finding in category["findings"]:
                key = (audit["key"], category["key"], finding.check_key)
                old = prev_statuses.get(key)
                new = str(finding.status)
                if old is not None and old != new:
                    changed.append(
                        {
                            "audit": audit["label"],
                            "check": finding.check_key,
                            "from": old,
                            "to": new,
                            "value": finding.value,
                            "_rank": _STATUS_RANK.get(new, 9) - _STATUS_RANK.get(old, 9),
                        }
                    )
    changed.sort(key=lambda c: c["_rank"])
    return {"changed_findings": changed}


def build_comparison(session: Session, audits: list[dict], previous: AuditRun) -> dict:
    """Compare the current per-audit view against a previous run (DB-backed wrapper)."""
    prev_scores, prev_statuses = _run_index(session, previous)
    return compare_audits(audits, prev_scores, prev_statuses)
