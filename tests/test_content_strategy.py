"""The content strategy audit, including its multiplicative headline scoring."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app import llm
from app.acquisition.crawler import CrawledPage
from app.acquisition.fetcher import Acquisition
from app.acquisition.render import RenderResult
from app.audits.base import AuditContext
from app.audits.content_strategy import ContentStrategyAudit, _is_content_url, _parse_date


def _ctx(sitemap_locs=(), lastmods=None, crawled=(), html="", connectors=None):
    acq = Acquisition(requested_url="https://example.com/")
    acq.sitemap_locs = list(sitemap_locs)
    acq.sitemap_lastmods = dict(lastmods or {})
    acq.html = html
    render = RenderResult(requested_url="https://example.com", html=html)
    return AuditContext(
        site_domain="example.com",
        data={"acquisition": acq, "render": render, "pages": list(crawled)},
        connectors=connectors or {},
    )


def _cat(result, key):
    return next(c for c in result.categories if c.key == key)


# --- helpers ---

def test_content_url_classification():
    assert _is_content_url("https://x.com/blog/how-to-grow")
    assert _is_content_url("https://x.com/insights/report")
    assert not _is_content_url("https://x.com/about")
    assert not _is_content_url("https://x.com/products/widget")


def test_parse_date_handles_date_and_timestamp():
    assert _parse_date("2024-03-15") == date(2024, 3, 15)
    assert _parse_date("2024-03-15T10:30:00+00:00") == date(2024, 3, 15)
    assert _parse_date("not-a-date") is None
    assert _parse_date("") is None


# --- multiplicative headline ---

def test_quality_gates_cadence_both_ways():
    audit = ContentStrategyAudit()
    both_high = audit._headline_score(100.0, 100.0, None, None)
    cadence_only = audit._headline_score(100.0, 25.0, None, None)
    quality_only = audit._headline_score(25.0, 100.0, None, None)
    # High on one axis alone cannot match high on both.
    assert both_high == 100.0
    assert cadence_only == 50.0  # geometric mean of 100 and 25
    assert quality_only == 50.0
    assert cadence_only < both_high


def test_headline_falls_back_when_one_half_missing():
    audit = ContentStrategyAudit()
    # No quality judgement (B None): headline rests on cadence alone, weighted with D.
    score = audit._headline_score(80.0, None, None, 100.0)
    assert score == (80.0 * 60 + 100.0 * 15) / 75


def test_headline_none_when_nothing_scorable():
    assert ContentStrategyAudit()._headline_score(None, None, None, None) is None


# --- categories end to end ---

def test_no_content_is_a_failure(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    result = ContentStrategyAudit().run(_ctx(html="<h1>brochure</h1>"))
    inventory = _cat(result, "publishing_cadence").checks[0]
    assert inventory.score == 0.0
    assert str(inventory.status) == "fail"
    # With no content B can't be judged and the headline collapses toward zero.
    assert result.score is not None and result.score < 20


def test_recency_and_regularity_from_lastmods(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    today = datetime.now(UTC).date()
    locs = [f"https://example.com/blog/post-{i}" for i in range(8)]
    lastmods = {loc: str(today - timedelta(days=7 * i)) for i, loc in enumerate(locs)}
    result = ContentStrategyAudit().run(_ctx(sitemap_locs=locs, lastmods=lastmods))
    cadence = _cat(result, "publishing_cadence")
    keys = {c.key for c in cadence.checks}
    assert {"content_inventory", "content_recency", "publishing_regularity"} <= keys
    recency = next(c for c in cadence.checks if c.key == "content_recency")
    assert recency.score == 100.0  # newest post is recent


def test_identical_lastmods_are_treated_as_unreliable(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    locs = [f"https://example.com/blog/post-{i}" for i in range(6)]
    lastmods = {loc: "2024-01-01" for loc in locs}
    result = ContentStrategyAudit().run(_ctx(sitemap_locs=locs, lastmods=lastmods))
    recency = next(
        c for c in _cat(result, "publishing_cadence").checks if c.key == "content_recency"
    )
    assert recency.score is None  # not scored from a single shared date
    assert "identical" in recency.value


def test_quality_uses_llm_when_available(monkeypatch):
    from app.audits import content_strategy as cs

    class _Verdict:
        relevance_score = 85
        on_topic = True
        note = "tightly focused on the buyer's questions"

    monkeypatch.setattr(cs.llm, "available", lambda: True)
    monkeypatch.setattr(cs.llm, "judge", lambda *a, **k: _Verdict())
    crawled = [CrawledPage("https://example.com/blog/grow", 1, 200, "How to grow")]
    result = ContentStrategyAudit().run(
        _ctx(sitemap_locs=["https://example.com/blog/grow"], crawled=crawled)
    )
    quality = _cat(result, "content_quality_icp")
    assert quality.score == 85.0


def test_performance_is_conditional_needs_connection(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    result = ContentStrategyAudit().run(_ctx(sitemap_locs=["https://example.com/blog/x"]))
    perf = _cat(result, "content_performance")
    assert perf.score is None
    assert perf.applicable is False


def test_conversion_pathway_detected(monkeypatch):
    monkeypatch.setattr(llm, "available", lambda: False)
    html = "<form></form> <a href='/contact'>Get started</a>"
    result = ContentStrategyAudit().run(
        _ctx(sitemap_locs=["https://example.com/blog/x"], html=html)
    )
    conv = _cat(result, "conversion_pathway")
    assert conv.score == 100.0
