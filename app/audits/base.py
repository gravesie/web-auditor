"""The interface every sub-audit module implements.

Scoring follows the spec (section 4) and architecture (section 6): each check
yields a 0-100 score plus a detection tag; a category score is the weighted mean
of its checks; the audit score is the weighted mean of its applicable categories,
conditional categories rebalanced out when they don't apply to the site.

This is the contract the first real audit (compliance or build-and-security) will
implement next. The shared acquisition dataset and the live connectors are passed
in via AuditContext so a module never fetches anything itself.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from app.models.enums import DetectionTag, FindingStatus, Severity


@dataclass
class CheckResult:
    key: str
    # 0..100, or None for an informational check that does not contribute to the score.
    score: float | None
    status: FindingStatus
    severity: Severity
    detection: DetectionTag
    value: str | None = None
    recommendation: str | None = None
    evidence: dict = field(default_factory=dict)


@dataclass
class CategoryDef:
    key: str
    label: str
    weight: float
    conditional: bool = False


@dataclass
class CategoryResult:
    key: str
    # None when the category has no scorable checks (not yet assessed). Such a
    # category is excluded from the audit score rather than counted as zero.
    score: float | None
    applicable: bool
    checks: list[CheckResult]


@dataclass
class AuditResult:
    audit_key: str
    score: float
    completeness: float  # 0..1, share backed by observed / connected data
    categories: list[CategoryResult]


@dataclass
class AuditContext:
    """The shared, gather-once dataset plus whatever connectors are live.

    Populated by the acquisition stage. Fields are added as that stage is built;
    kept loose for now so the interface can land before acquisition exists.
    """

    site_domain: str
    data: dict = field(default_factory=dict)
    connectors: dict = field(default_factory=dict)


class AuditModule(ABC):
    key: str
    label: str
    categories: list[CategoryDef]

    @abstractmethod
    def run(self, context: AuditContext) -> AuditResult:
        """Produce findings and category scores from the shared dataset."""
        raise NotImplementedError
