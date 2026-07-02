"""Per-plan site limits."""

from __future__ import annotations

from app.plans import at_site_limit, max_sites_for


def test_known_limits():
    assert max_sites_for("free") == 5
    assert max_sites_for("internal") is None  # unlimited
    assert max_sites_for("pro") == 100


def test_unknown_plan_defaults_to_free():
    assert max_sites_for("mystery") == 5


def test_at_limit_on_free():
    assert not at_site_limit(4, "free")
    assert at_site_limit(5, "free")
    assert at_site_limit(6, "free")


def test_unlimited_plan_never_at_limit():
    assert not at_site_limit(10_000, "internal")
