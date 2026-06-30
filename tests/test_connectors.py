"""Connector layer: credential crypto, GSC/GA4 response parsing, and the audit
consumers that turn live data into observed checks. All offline and deterministic."""

from datetime import UTC, datetime

import pytest

from app.acquisition.crawler import CrawledPage
from app.audits.on_page_seo import _ranking_checks_from_gsc
from app.audits.technical_seo import _indexed_count_check
from app.connectors import ga4, gsc
from app.models.enums import DetectionTag, FindingStatus
from app.security import crypto

# --- crypto ---------------------------------------------------------------------

def test_encrypt_decrypt_roundtrip():
    token = crypto.encrypt("secret-value")
    assert token != "secret-value"
    assert crypto.decrypt(token) == "secret-value"


def test_encrypt_json_roundtrip():
    payload = {"refresh_token": "abc", "resource_id": "sc-domain:example.com"}
    assert crypto.decrypt_json(crypto.encrypt_json(payload)) == payload


def test_decrypt_rejects_tampered_token():
    token = crypto.encrypt("secret-value")
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt(token[:-4] + "0000")


def test_decrypt_rejects_garbage():
    with pytest.raises(crypto.DecryptionError):
        crypto.decrypt("not-a-real-token")


def test_decrypt_ttl_accepts_fresh_token():
    token = crypto.encrypt("x")
    assert crypto.decrypt_ttl(token, 600) == "x"


# --- GSC parsing ----------------------------------------------------------------

def test_default_window_applies_lag_and_length():
    start, end = gsc.default_window(datetime(2026, 6, 29, tzinfo=UTC))
    assert end == "2026-06-27"  # two-day lag
    assert start == "2026-05-31"  # 28-day window inclusive


def test_summarise_queries_totals_and_weighted_position():
    rows = [
        {"keys": ["a"], "clicks": 10, "impressions": 100, "ctr": 0.1, "position": 2.0},
        {"keys": ["b"], "clicks": 0, "impressions": 100, "ctr": 0.0, "position": 12.0},
    ]
    summary = gsc.summarise_queries(rows)
    assert summary.total_clicks == 10
    assert summary.total_impressions == 200
    assert summary.avg_position == 7.0  # (2*100 + 12*100) / 200
    # "b" is in striking distance (position 12), "a" (position 2) is not.
    assert [q["query"] for q in summary.striking_distance] == ["b"]


def test_summarise_queries_handles_no_impressions():
    summary = gsc.summarise_queries([])
    assert summary.total_impressions == 0
    assert summary.avg_position is None


def test_count_serving_pages_counts_pages_with_impressions():
    rows = [
        {"keys": ["https://x/a"], "impressions": 40},
        {"keys": ["https://x/b"], "impressions": 1},
        {"keys": ["https://x/c"], "impressions": 0},  # served 0 impressions -> not counted
    ]
    assert gsc.count_serving_pages(rows) == 2


def test_count_serving_pages_zero_without_data():
    assert gsc.count_serving_pages([]) == 0


# --- GA4 parsing ----------------------------------------------------------------

def test_summarise_report_maps_by_metric_name():
    report = {
        "metricHeaders": [
            {"name": "sessions"},
            {"name": "totalUsers"},
            {"name": "engagementRate"},
            {"name": "averageSessionDuration"},
            {"name": "keyEvents"},
        ],
        "rows": [
            {"metricValues": [{"value": "120"}, {"value": "95"}, {"value": "0.62"},
                              {"value": "73.4"}, {"value": "7"}]}
        ],
    }
    summary = ga4.summarise_report(report)
    assert summary.sessions == 120
    assert summary.total_users == 95
    assert summary.engagement_rate == 0.62
    assert summary.key_events == 7


def test_summarise_report_empty_is_zeroed_not_error():
    summary = ga4.summarise_report({"metricHeaders": [], "rows": []})
    assert summary.sessions == 0
    assert summary.engagement_rate is None


def test_property_id_normalised():
    client = ga4.Ga4Client("token", "properties/123456789")
    assert client.property_id == "123456789"


# --- audit consumers ------------------------------------------------------------

def _pages(n_ok: int) -> list[CrawledPage]:
    return [CrawledPage(url=f"https://x/{i}", depth=1, status=200) for i in range(n_ok)]


def test_indexed_count_needs_connection_without_gsc():
    check = _indexed_count_check(None, _pages(5))
    assert check.score is None
    assert check.detection == DetectionTag.needs_connection


def test_indexed_count_observed_when_pages_serving():
    check = _indexed_count_check({"serving_pages": 10}, _pages(10))
    assert check.score == 100.0
    assert check.detection == DetectionTag.observed
    assert check.status == FindingStatus.passed


def test_indexed_count_zero_serving_is_not_a_fail():
    # No impressions in the window can't prove de-indexation, so it must not fail.
    check = _indexed_count_check({"serving_pages": 0}, _pages(10))
    assert check.score is None
    assert check.status == FindingStatus.info
    assert check.detection == DetectionTag.observed


def test_indexed_count_warns_when_under_serving():
    # 3 serving vs 10 crawled is below the 80% threshold.
    check = _indexed_count_check({"serving_pages": 3}, _pages(10))
    assert check.score == 70.0
    assert check.status == FindingStatus.warn


def test_ranking_checks_reward_clicks():
    gsc_data = {
        "total_clicks": 50, "total_impressions": 1000, "avg_position": 4.2,
        "striking_distance": [{"query": "widget repair"}],
    }
    checks = _ranking_checks_from_gsc(gsc_data)
    by_key = {c.key: c for c in checks}
    assert by_key["search_visibility"].score == 100.0
    assert by_key["average_position"].score == 100.0  # position <= 5
    # striking distance is an opportunity list, not scored.
    assert by_key["striking_distance"].score is None
    assert by_key["striking_distance"].detection == DetectionTag.observed


def test_ranking_checks_flag_impressions_without_clicks():
    checks = _ranking_checks_from_gsc(
        {"total_clicks": 0, "total_impressions": 500, "avg_position": 18.0,
         "striking_distance": []}
    )
    by_key = {c.key: c for c in checks}
    assert by_key["search_visibility"].status == FindingStatus.warn
    assert by_key["average_position"].score == 55.0  # 10 < pos <= 20


def test_ranking_checks_fail_when_no_impressions():
    checks = _ranking_checks_from_gsc(
        {"total_clicks": 0, "total_impressions": 0, "avg_position": None,
         "striking_distance": []}
    )
    by_key = {c.key: c for c in checks}
    assert by_key["search_visibility"].status == FindingStatus.fail
    # No average_position check when there were no impressions.
    assert "average_position" not in by_key
