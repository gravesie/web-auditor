"""The scoring engine: category means, weighted rollup, exclusion and completeness."""

from app import scoring
from app.audits.base import CategoryDef, CategoryResult, CheckResult
from app.models.enums import DetectionTag, FindingStatus, Severity


def _check(score, detection=DetectionTag.observed):
    return CheckResult("k", score, FindingStatus.info, Severity.info, detection)


def test_category_score_is_mean_of_scored_checks():
    assert scoring.category_score([_check(100.0), _check(50.0)]) == 75.0


def test_category_score_none_when_nothing_scorable():
    assert scoring.category_score([_check(None), _check(None)]) is None
    assert scoring.category_score([]) is None


def test_audit_score_weighted_mean():
    defs = {"a": CategoryDef("a", "A", 30), "b": CategoryDef("b", "B", 10)}
    cats = [CategoryResult("a", 80.0, True, []), CategoryResult("b", 40.0, True, [])]
    # (80*30 + 40*10) / 40 = 70
    assert scoring.audit_score(cats, defs) == 70.0


def test_audit_score_excludes_none_and_non_applicable_and_rebalances():
    defs = {
        "a": CategoryDef("a", "A", 30),
        "b": CategoryDef("b", "B", 10),
        "c": CategoryDef("c", "C", 10, conditional=True),
    }
    cats = [
        CategoryResult("a", 80.0, True, []),   # counts
        CategoryResult("b", None, True, []),    # no score -> excluded
        CategoryResult("c", 0.0, False, []),    # not applicable -> excluded
    ]
    # only A is scorable, so the result is A's score regardless of the other weights
    assert scoring.audit_score(cats, defs) == 80.0


def test_audit_score_none_when_no_category_scorable():
    defs = {"a": CategoryDef("a", "A", 30)}
    cats = [CategoryResult("a", None, True, [])]
    assert scoring.audit_score(cats, defs) is None


def test_completeness_is_observed_fraction():
    checks = [_check(100.0, DetectionTag.observed), _check(50.0, DetectionTag.inferred)]
    cats = [CategoryResult("a", 75.0, True, checks)]
    assert scoring.completeness(cats) == 0.5


def test_completeness_ignores_unscored_and_non_applicable():
    cats = [
        CategoryResult("a", 100.0, True, [_check(100.0, DetectionTag.observed)]),
        CategoryResult("b", None, True, [_check(None, DetectionTag.inferred)]),
        CategoryResult("c", 0.0, False, [_check(50.0, DetectionTag.inferred)]),
    ]
    assert scoring.completeness(cats) == 1.0
