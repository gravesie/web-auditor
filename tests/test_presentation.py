"""The page-one presentation model: funnel roll-up, grading and commercial ordering."""

from __future__ import annotations

from app.remediation.ranking import ActionItem
from app.reporting.presentation import assemble_page_one


def _fix(audit_key, check_key, impact=4, effort=1, severity="medium"):
    return ActionItem(
        audit_key=audit_key,
        audit_label=audit_key,
        category="cat",
        check_key=check_key,
        impact=impact,
        effort=effort,
        why="why",
        how="how",
        severity=severity,
        status="fail",
        detection="observed",
        value="v",
        finding_id="1",
    )


def _scores(**kw):
    # Default every audit to None, override the ones a test cares about.
    base = {
        "technical_seo": None, "on_page_seo": None, "schema": None, "geo": None,
        "performance": None, "content_quality": None, "content_strategy": None,
        "messaging": None, "analytics": None, "build_security": None, "compliance": None,
    }
    base.update(kw)
    return base


def test_grade_bands():
    assert assemble_page_one(_scores(), 90.0, []).grade == "A"
    assert assemble_page_one(_scores(), 72.0, []).grade == "B"
    assert assemble_page_one(_scores(), 60.0, []).grade == "C"
    assert assemble_page_one(_scores(), 45.0, []).grade == "D"
    assert assemble_page_one(_scores(), 30.0, []).grade == "E"
    assert assemble_page_one(_scores(), 10.0, []).grade == "F"
    assert assemble_page_one(_scores(), None, []).grade == "n/a"


def test_pillar_is_weighted_mean_of_members():
    # get_found holds technical_seo (weight 12) and on_page_seo (weight 13).
    view = assemble_page_one(_scores(technical_seo=80.0, on_page_seo=60.0), 70.0, [])
    found = next(p for p in view.pillars if p.key == "get_found")
    assert round(found.score, 1) == round((80.0 * 12 + 60.0 * 13) / 25, 1)


def test_pillar_status_thresholds():
    good = assemble_page_one(_scores(messaging=75.0), 75.0, [])
    watch = assemble_page_one(_scores(messaging=55.0), 55.0, [])
    problem = assemble_page_one(_scores(messaging=40.0), 40.0, [])
    assert next(p for p in good.pillars if p.key == "convert").status == "good"
    assert next(p for p in watch.pillars if p.key == "convert").status == "watch"
    assert next(p for p in problem.pillars if p.key == "convert").status == "problem"


def test_compliance_fixes_are_demoted_from_page_one():
    # Compliance leads the raw ranking (impact 5) but must not lead the owner's page.
    fixes = [
        _fix("compliance", "trackers_before_consent", impact=5, severity="high"),
        _fix("on_page_seo", "title_tag", impact=4),
        _fix("messaging", "cta_present", impact=4),
    ]
    view = assemble_page_one(_scores(), 60.0, fixes)
    top_keys = [f.check_key for f in view.top_fixes]
    assert top_keys[0] != "trackers_before_consent"
    assert "trackers_before_consent" in top_keys  # demoted, not dropped


def test_severe_security_stays_visible():
    # Action items arrive already ranked (impact 5 first). A critical security issue is
    # not demoted, so it keeps its lead; the routine-security test below is the contrast.
    fixes = [
        _fix("build_security", "exposed_paths", impact=5, severity="critical"),
        _fix("on_page_seo", "title_tag", impact=4),
    ]
    view = assemble_page_one(_scores(), 60.0, fixes)
    assert view.top_fixes[0].check_key == "exposed_paths"


def test_routine_security_is_demoted():
    fixes = [
        _fix("build_security", "header_referrer_policy", impact=1, severity="low"),
        _fix("performance", "cwv_lcp", impact=4),
    ]
    view = assemble_page_one(_scores(), 60.0, fixes)
    assert view.top_fixes[0].check_key == "cwv_lcp"


def test_strengths_pick_high_scoring_audits_capped_at_three():
    view = assemble_page_one(
        _scores(performance=90.0, technical_seo=85.0, content_quality=80.0, schema=78.0),
        83.0,
        [],
    )
    assert len(view.strengths) == 3
    assert "Fast, responsive pages." in view.strengths


def test_verdict_names_the_weak_pillar_when_a_pillar_is_a_problem():
    view = assemble_page_one(_scores(messaging=35.0, technical_seo=85.0), 60.0, [])
    assert "convert" in view.verdict.lower()
