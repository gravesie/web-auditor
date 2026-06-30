"""The prioritised action list: deterministic ranking of a run's live findings.

The spec is firm that the ranking is deterministic, driven only by the catalogue's
impact and effort values, so the same site always produces the same priorities. An
LLM may write narrative around this list later, but never reorders it.

We rank the findings that are actionable now (status fail or warn). Each is joined
to its catalogue entry by composite key. The sort is impact descending, then effort
ascending, so high-impact quick wins come first; severity and the composite key
break ties so the order is fully stable.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Finding
from app.models.enums import FindingStatus
from app.remediation.catalogue import lookup
from app.remediation.schema import RemediationEntry

# Findings worth acting on. Passing checks and informational notes are not actions.
_ACTIONABLE = {FindingStatus.fail, FindingStatus.warn}

# Lower sorts first, so a more severe finding wins at equal impact and effort.
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


@dataclass
class ActionItem:
    audit_key: str
    audit_label: str
    category: str
    check_key: str
    impact: int
    effort: int
    why: str
    how: str
    severity: str
    status: str
    detection: str
    value: str | None
    finding_id: str


def rank_findings(
    findings: list[tuple[Finding, str, str]],
) -> list[ActionItem]:
    """Rank (finding, audit_key, audit_label) triples into the action list.

    Findings with no catalogue entry are skipped rather than guessed at; the coverage
    test guarantees every emitted check has an entry, so a skip means the finding
    wasn't actionable (its key resolved to nothing only if the catalogue is wrong).
    """
    items: list[ActionItem] = []
    for finding, audit_key, audit_label in findings:
        if finding.status not in _ACTIONABLE:
            continue
        entry: RemediationEntry | None = lookup(audit_key, finding.check_key)
        if entry is None:
            continue
        items.append(
            ActionItem(
                audit_key=audit_key,
                audit_label=audit_label,
                category=finding.category,
                check_key=finding.check_key,
                impact=entry.impact,
                effort=entry.effort,
                why=entry.why,
                how=entry.how,
                severity=str(finding.severity),
                status=str(finding.status),
                detection=str(finding.detection_tag),
                value=finding.value,
                finding_id=str(finding.id),
            )
        )

    items.sort(
        key=lambda a: (
            -a.impact,
            a.effort,
            _SEVERITY_RANK.get(a.severity, 9),
            f"{a.audit_key}.{a.check_key}",
        )
    )
    return items
