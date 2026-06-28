"""Run-to-run comparison: score deltas and finding status changes."""

from types import SimpleNamespace

from app.models.enums import FindingStatus
from app.reporting.view import compare_audits


def _finding(check_key, status, value="v"):
    return SimpleNamespace(check_key=check_key, status=status, value=value)


def _audits():
    return [
        {
            "key": "compliance",
            "label": "Compliance",
            "score": 48.0,
            "categories": [
                {
                    "key": "cookies_consent",
                    "findings": [
                        _finding("trackers_before_consent", FindingStatus.fail),
                        _finding("consent_mechanism", FindingStatus.passed),
                    ],
                }
            ],
        }
    ]


def test_score_delta_annotated():
    audits = _audits()
    compare_audits(audits, {"compliance": 60.0}, {})
    assert audits[0]["delta"] == -12.0


def test_delta_none_when_no_previous_score():
    audits = _audits()
    compare_audits(audits, {}, {})
    assert audits[0]["delta"] is None


def test_status_change_detected_regression_first():
    prev_statuses = {
        ("compliance", "cookies_consent", "trackers_before_consent"): "pass",  # was pass, now fail
        ("compliance", "cookies_consent", "consent_mechanism"): "warn",        # was warn, now pass
    }
    result = compare_audits(_audits(), {"compliance": 60.0}, prev_statuses)
    changed = result["changed_findings"]
    assert len(changed) == 2
    # regression (pass -> fail) ranks before the improvement (warn -> pass)
    assert changed[0]["check"] == "trackers_before_consent"
    assert changed[0]["from"] == "pass"
    assert changed[0]["to"] == "fail"


def test_unchanged_status_not_reported():
    prev_statuses = {
        ("compliance", "cookies_consent", "trackers_before_consent"): "fail",
        ("compliance", "cookies_consent", "consent_mechanism"): "pass",
    }
    result = compare_audits(_audits(), {"compliance": 48.0}, prev_statuses)
    assert result["changed_findings"] == []
