"""Commercial weighting of the site score."""

from __future__ import annotations

import pytest

from app.runner import AUDIT_MODULES
from app.weighting import AUDIT_WEIGHTS, weighted_site_score


def test_weighted_mean():
    # on_page_seo weight 13 at 80, build_security weight 5 at 60.
    score, contributions = weighted_site_score([("on_page_seo", 80.0), ("build_security", 60.0)])
    expected = (80.0 * 13 + 60.0 * 5) / 18
    assert score == pytest.approx(expected)
    assert sum(c for c in contributions.values() if c is not None) == pytest.approx(score)


def test_unscored_audits_are_excluded_and_rebalanced():
    # The None-scored audit must not drag the score down; the result matches the two
    # scored audits on their own.
    with_none = weighted_site_score(
        [("on_page_seo", 80.0), ("build_security", 60.0), ("performance", None)]
    )[0]
    without = weighted_site_score([("on_page_seo", 80.0), ("build_security", 60.0)])[0]
    assert with_none == pytest.approx(without)


def test_unscored_audit_contribution_is_none():
    _, contributions = weighted_site_score([("on_page_seo", 80.0), ("performance", None)])
    assert contributions["performance"] is None
    assert contributions["on_page_seo"] == pytest.approx(80.0)


def test_none_when_nothing_scored():
    score, contributions = weighted_site_score([("on_page_seo", None), ("performance", None)])
    assert score is None
    assert all(c is None for c in contributions.values())


def test_higher_weighted_audit_pulls_the_score():
    # Same two scores, weights swapped by audit: the heavier-weighted low score pulls
    # the mean below a plain average (70).
    low_on_heavy = weighted_site_score([("on_page_seo", 60.0), ("schema", 80.0)])[0]
    assert low_on_heavy < 70.0


def test_weights_sum_to_100():
    assert sum(AUDIT_WEIGHTS.values()) == 100


def test_every_registered_audit_has_a_weight():
    missing = [m.key for m in AUDIT_MODULES if m.key not in AUDIT_WEIGHTS]
    assert not missing, f"registered audits with no commercial weight: {missing}"
