"""The analytics and measurement audit."""

from __future__ import annotations

from app.acquisition.render import RenderResult, RequestRecord
from app.audits.analytics import AnalyticsAudit
from app.audits.base import AuditContext


def _req(url):
    return RequestRecord(url=url, resource_type="script", method="GET", third_party=True)


def _context(urls=(), html="", connectors=None):
    render = RenderResult(
        requested_url="https://example.com",
        html=html,
        requests=[_req(u) for u in urls],
    )
    return AuditContext(
        site_domain="example.com",
        data={"render": render},
        connectors=connectors or {},
    )


def _cat(result, key):
    return next(c for c in result.categories if c.key == key)


def test_full_stack_scores_well():
    ctx = _context(
        urls=[
            "https://www.google-analytics.com/g/collect",
            "https://connect.facebook.net/en_US/fbevents.js",
            "https://static.hotjar.com/c/hotjar-123.js",
            "https://www.googletagmanager.com/gtm.js?id=GTM-X",
        ]
    )
    result = AnalyticsAudit().run(ctx)
    assert result.score == 100.0
    assert _cat(result, "web_analytics_presence").score == 100.0
    assert _cat(result, "conversion_campaign").score == 100.0


def test_no_analytics_is_the_headline_failure():
    result = AnalyticsAudit().run(_context(html="<h1>brochure site</h1>"))
    web = _cat(result, "web_analytics_presence")
    assert web.score == 0.0
    assert str(web.checks[0].status) == "fail"
    assert str(web.checks[0].severity) == "high"
    # Missing-but-not-fatal layers score down without failing.
    assert _cat(result, "behavioural_product").score == 60.0


def test_ga4_connector_confirms_presence_without_on_page_tag():
    # A tag manager can hide the on-page GA signature; the connector still confirms it.
    result = AnalyticsAudit().run(_context(html="<h1>hi</h1>", connectors={"ga4": object()}))
    web = _cat(result, "web_analytics_presence")
    assert web.score == 100.0
    assert "connected" in web.checks[0].value


def test_unrenderable_page_is_not_assessed():
    result = AnalyticsAudit().run(_context())
    assert result.score is None
    assert result.completeness == 0.0
