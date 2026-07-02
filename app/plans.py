"""Per-plan limits.

Small config for how many sites an account may hold, keyed by plan tier. Goyande's
own account is "internal" (unlimited); the free customer plan is capped at five, per
the onboarding brief. An unknown tier is treated as free, the safe default.
"""

from __future__ import annotations

# plan_tier -> maximum sites, or None for unlimited.
PLAN_SITE_LIMITS: dict[str, int | None] = {
    "internal": None,
    "free": 5,
    "starter": 25,
    "pro": 100,
}
_DEFAULT_LIMIT = 5


def max_sites_for(plan_tier: str) -> int | None:
    """Site cap for a plan, or None for unlimited."""
    return PLAN_SITE_LIMITS.get(plan_tier, _DEFAULT_LIMIT)


def at_site_limit(current_count: int, plan_tier: str) -> bool:
    """True when the account has reached its plan's site cap."""
    limit = max_sites_for(plan_tier)
    return limit is not None and current_count >= limit
