"""Generic scoring helpers shared by every audit module.

Within a category, checks are equal-weighted (the spec defines weights at the
category level, not the check level). Across categories, the audit score is the
weighted mean of the applicable categories, with non-applicable ones rebalanced
out, exactly as conditional categories behave. Informational checks (score None)
are excluded.
"""

from app.audits.base import CategoryDef, CategoryResult, CheckResult
from app.models.enums import DetectionTag


def category_score(checks: list[CheckResult]) -> float | None:
    """Mean of the scorable checks, or None if the category has none."""
    scored = [c.score for c in checks if c.score is not None]
    if not scored:
        return None
    return sum(scored) / len(scored)


def audit_score(categories: list[CategoryResult], defs: dict[str, CategoryDef]) -> float:
    """Weighted mean over categories that both apply and have a score.

    A category with no scorable checks is excluded and its weight rebalanced
    away, the same as a conditional category that does not apply.
    """
    weighted = 0.0
    total_weight = 0.0
    for cat in categories:
        if not cat.applicable or cat.score is None:
            continue
        weight = defs[cat.key].weight
        weighted += cat.score * weight
        total_weight += weight
    return weighted / total_weight if total_weight else 0.0


def completeness(categories: list[CategoryResult]) -> float:
    """Share of scored checks that were observed rather than inferred.

    Reflects how much of the audit is backed by direct evidence. Informational
    checks (score None) are ignored.
    """
    checks = [
        c
        for cat in categories
        if cat.applicable and cat.score is not None
        for c in cat.checks
        if c.score is not None
    ]
    if not checks:
        return 0.0
    observed = [c for c in checks if c.detection == DetectionTag.observed]
    return len(observed) / len(checks)
