"""The action list is deterministic and impact-first."""

from __future__ import annotations

from types import SimpleNamespace

from app.models.enums import DetectionTag, FindingStatus, Severity
from app.remediation.ranking import rank_findings


def _finding(check_key, *, status=FindingStatus.fail, severity=Severity.high, value="x", fid="1"):
    return SimpleNamespace(
        check_key=check_key,
        category="cat",
        status=status,
        severity=severity,
        detection_tag=DetectionTag.observed,
        value=value,
        id=fid,
    )


def _triple(check_key, audit_key="on_page_seo", **kwargs):
    return (_finding(check_key, **kwargs), audit_key, audit_key)


def test_orders_by_impact_then_effort():
    items = rank_findings(
        [
            _triple("striking_distance"),               # impact 5, effort 2
            _triple("homepage_indexable", "technical_seo"),  # impact 5, effort 1
            _triple("title_tag"),                        # impact 4, effort 1
            _triple("copyright_year", "build_security"),  # impact 1, effort 1
        ]
    )
    assert [i.check_key for i in items] == [
        "homepage_indexable",  # 5,1
        "striking_distance",   # 5,2
        "title_tag",           # 4,1
        "copyright_year",      # 1,1
    ]


def test_excludes_passing_and_informational_findings():
    items = rank_findings(
        [
            _triple("title_tag", status=FindingStatus.passed),
            _triple("striking_distance", status=FindingStatus.info),
            _triple("homepage_indexable", "technical_seo", status=FindingStatus.warn),
        ]
    )
    assert [i.check_key for i in items] == ["homepage_indexable"]


def test_skips_findings_with_no_catalogue_entry():
    items = rank_findings([_triple("not_a_real_check")])
    assert items == []


def test_is_stable_for_equal_impact_and_effort():
    # Two checks at the same impact and effort must order by severity then composite key,
    # the same way every time regardless of input order.
    forward = rank_findings(
        [
            _triple("header_x_frame_options", "build_security"),       # 2,1 medium-ish
            _triple("image_alt"),                                      # 2,1
        ]
    )
    backward = rank_findings(
        [
            _triple("image_alt"),
            _triple("header_x_frame_options", "build_security"),
        ]
    )
    assert [i.check_key for i in forward] == [i.check_key for i in backward]


def test_carries_catalogue_text_and_ratings():
    (item,) = rank_findings([_triple("striking_distance")])
    assert item.impact == 5
    assert item.effort == 2
    assert "page one" in item.how
    assert item.why
