"""Commercial weighting of the site score.

The site score is a weighted mean of the sub-audit scores, not a plain average.
The weights reflect commercial leverage, agreed for spec V0-7 and summing to 100.
They live here as the single place to tune them; they can move to per-account
config when the product goes multi-user.

Audits that produced no score (an external source was unavailable, say) are
excluded and the remaining weights rebalanced, the same rule the per-audit scorer
uses for conditional categories. Two of the weighted audits, content_strategy and
analytics, are defined here ahead of being built (build sequence step 2); until
they exist they simply don't appear in a run and their weight rebalances out.
"""

from __future__ import annotations

# audit_key -> commercial weight. Sums to 100 across all eleven v1 sub-audits.
AUDIT_WEIGHTS: dict[str, int] = {
    "on_page_seo": 13,
    "content_strategy": 13,
    "messaging": 12,
    "technical_seo": 12,
    "performance": 9,
    "geo": 9,
    "content_quality": 8,
    "analytics": 7,
    "compliance": 7,
    "schema": 5,
    "build_security": 5,
}

# Fallback for an audit with no weight set. The coverage test stops a registered
# audit from relying on this silently; it only guards against an outright crash.
_DEFAULT_WEIGHT = 8


def weight_for(audit_key: str) -> int:
    return AUDIT_WEIGHTS.get(audit_key, _DEFAULT_WEIGHT)


def weighted_site_score(
    scores: list[tuple[str, float | None]],
) -> tuple[float | None, dict[str, float | None]]:
    """Site score and each audit's weighted contribution.

    Takes (audit_key, score) pairs. Returns the weighted-mean site score (None when
    nothing scored) and a map of audit_key -> points contributed to that score
    (None for an unscored audit). The contributions sum to the site score.
    """
    scored = [(key, score) for key, score in scores if score is not None]
    total_weight = sum(weight_for(key) for key, _ in scored)
    if not total_weight:
        return None, {key: None for key, _ in scores}

    contributions: dict[str, float | None] = {}
    site_score = 0.0
    for key, score in scores:
        if score is None:
            contributions[key] = None
            continue
        share = weight_for(key) / total_weight
        contribution = score * share
        contributions[key] = contribution
        site_score += contribution
    return site_score, contributions
