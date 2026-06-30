"""The shared tag-fingerprint signature matching."""

from __future__ import annotations

from app.acquisition.render import RenderResult, RequestRecord
from app.fingerprint import ANALYTICS, CONVERSION, INFRA, detect_platforms


def _req(url):
    return RequestRecord(url=url, resource_type="script", method="GET", third_party=True)


def _render(urls=(), html=""):
    return RenderResult(
        requested_url="https://example.com",
        html=html,
        requests=[_req(u) for u in urls],
    )


def _keys(detections):
    return {d.key for d in detections}


def test_detects_ga4_from_request():
    dets = detect_platforms(_render(urls=["https://www.google-analytics.com/g/collect?v=2"]))
    assert "ga4" in _keys(dets)
    assert next(d for d in dets if d.key == "ga4").category == ANALYTICS


def test_detects_meta_pixel_from_inline_html():
    dets = detect_platforms(_render(html="<script>fbq('init', '123');</script>"))
    assert "meta_pixel" in _keys(dets)
    assert next(d for d in dets if d.key == "meta_pixel").category == CONVERSION


def test_distinguishes_gtm_from_ga4():
    gtm = detect_platforms(
        _render(urls=["https://www.googletagmanager.com/gtm.js?id=GTM-ABC123"])
    )
    assert "gtm" in _keys(gtm)
    assert next(d for d in gtm if d.key == "gtm").category == INFRA
    assert "ga4" not in _keys(gtm)

    ga = detect_platforms(
        _render(urls=["https://www.googletagmanager.com/gtag/js?id=G-ABC123"])
    )
    assert "ga4" in _keys(ga)


def test_clean_page_detects_nothing():
    clean = _render(urls=["https://example.com/style.css"], html="<h1>hi</h1>")
    assert detect_platforms(clean) == []


def test_one_detection_per_platform():
    # GA4 matches both a request and an html pattern; it must appear once.
    dets = detect_platforms(
        _render(urls=["https://www.google-analytics.com/g/collect"], html="gtag('config', 'g-x')")
    )
    assert sum(1 for d in dets if d.key == "ga4") == 1
