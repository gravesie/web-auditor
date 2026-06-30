"""Shared tag-fingerprint module.

Identifies the third-party platforms a site loads, by matching the rendered page's
network requests and HTML against a signature table. The spec (13.11) makes this a
shared module: the analytics audit names what's installed, compliance (13.1) cares
that trackers fire, and the build fingerprint (13.2) cares what the stack is. This
module is the single signature source so those three never drift apart.

Detection is signature-based and deliberately conservative. A platform is reported
only when a request URL or an HTML snippet matches a known pattern; we never guess
from a bare domain that could mean several things. Matching is case-insensitive
substring matching, which is robust to query strings and minor URL changes.

Categories map to the analytics audit's four layers: analytics (is anything
measuring traffic at all), conversion (ad and goal tracking), behavioural (heatmaps
and session tools), and infra (tag managers and customer-data platforms).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.acquisition.render import RenderResult

ANALYTICS = "analytics"
CONVERSION = "conversion"
BEHAVIOURAL = "behavioural"
INFRA = "infra"


@dataclass(frozen=True)
class PlatformSig:
    key: str
    name: str
    category: str
    # Substrings matched (lowercased) against request URLs and the page HTML.
    url_patterns: tuple[str, ...] = ()
    html_patterns: tuple[str, ...] = ()


@dataclass
class Detection:
    key: str
    name: str
    category: str
    evidence: str  # the pattern that matched


# The signature table. Patterns are lowercase. Order is grouped by category for
# readability; detection does not depend on order.
PLATFORMS: list[PlatformSig] = [
    # --- Web analytics ---
    PlatformSig(
        "ga4", "Google Analytics 4", ANALYTICS,
        ("google-analytics.com", "/g/collect", "googletagmanager.com/gtag/js"),
        ("gtag('config'", "google-analytics.com"),
    ),
    PlatformSig(
        "adobe_analytics", "Adobe Analytics", ANALYTICS,
        ("omtrdc.net", "2o7.net", "demdex.net"),
        ("appmeasurement", "s_code.js"),
    ),
    PlatformSig(
        "plausible", "Plausible", ANALYTICS,
        ("plausible.io/js", "plausible.io/api/event"),
        ("plausible.io",),
    ),
    PlatformSig(
        "fathom", "Fathom Analytics", ANALYTICS,
        ("usefathom.com",),
        ("usefathom.com",),
    ),
    PlatformSig(
        "matomo", "Matomo", ANALYTICS,
        ("matomo.js", "matomo.php", "piwik.js", "piwik.php"),
        ("_paq", "matomo.js"),
    ),
    # --- Conversion and campaign ---
    PlatformSig(
        "meta_pixel", "Meta Pixel", CONVERSION,
        ("connect.facebook.net", "facebook.com/tr"),
        ("fbq('init'", "fbq("),
    ),
    PlatformSig(
        "linkedin_insight", "LinkedIn Insight Tag", CONVERSION,
        ("snap.licdn.com", "px.ads.linkedin.com"),
        ("_linkedin_partner_id",),
    ),
    PlatformSig(
        "google_ads", "Google Ads", CONVERSION,
        ("googleadservices.com", "googleads.g.doubleclick.net"),
        ("gtag('config', 'aw-", "googleadservices.com"),
    ),
    PlatformSig(
        "tiktok_pixel", "TikTok Pixel", CONVERSION,
        ("analytics.tiktok.com",),
        ("ttq.load", "ttq.track"),
    ),
    PlatformSig(
        "twitter_pixel", "X (Twitter) Pixel", CONVERSION,
        ("static.ads-twitter.com", "t.co/i/adsct"),
        ("twq('", "twq("),
    ),
    PlatformSig(
        "bing_uet", "Microsoft Advertising UET", CONVERSION,
        ("bat.bing.com",),
        ("uetq", "bat.bing.com"),
    ),
    PlatformSig(
        "pinterest_tag", "Pinterest Tag", CONVERSION,
        ("ct.pinterest.com", "s.pinimg.com/ct"),
        ("pintrk(",),
    ),
    # --- Behavioural and product ---
    PlatformSig(
        "hotjar", "Hotjar", BEHAVIOURAL,
        ("static.hotjar.com", "script.hotjar.com"),
        ("hotjar",),
    ),
    PlatformSig(
        "clarity", "Microsoft Clarity", BEHAVIOURAL,
        ("clarity.ms",),
        ("clarity.ms/tag",),
    ),
    PlatformSig(
        "fullstory", "Fullstory", BEHAVIOURAL,
        ("fullstory.com",),
        ("fs.identify", "fullstory"),
    ),
    PlatformSig(
        "logrocket", "LogRocket", BEHAVIOURAL,
        ("cdn.logrocket.io", "cdn.lr-in.com"),
        ("logrocket",),
    ),
    PlatformSig(
        "mixpanel", "Mixpanel", BEHAVIOURAL,
        ("cdn.mxpnl.com", "api.mixpanel.com"),
        ("mixpanel",),
    ),
    PlatformSig(
        "amplitude", "Amplitude", BEHAVIOURAL,
        ("cdn.amplitude.com", "api.amplitude.com", "api2.amplitude.com"),
        ("amplitude.getinstance", "amplitude.init"),
    ),
    PlatformSig(
        "heap", "Heap", BEHAVIOURAL,
        ("cdn.heapanalytics.com", "heapanalytics.com"),
        ("heap.load", "heapanalytics"),
    ),
    # --- Data infrastructure and governance ---
    PlatformSig(
        "gtm", "Google Tag Manager", INFRA,
        ("googletagmanager.com/gtm.js",),
        ("googletagmanager.com/gtm.js",),
    ),
    PlatformSig(
        "tealium", "Tealium", INFRA,
        ("tags.tiqcdn.com", "tiqcdn.com"),
        ("utag.js", "tiqcdn"),
    ),
    PlatformSig(
        "segment", "Segment", INFRA,
        ("cdn.segment.com", "api.segment.io"),
        ("cdn.segment.com",),
    ),
    PlatformSig(
        "rudderstack", "RudderStack", INFRA,
        ("rudderlabs.com", "rudderstack.com"),
        ("rudderanalytics",),
    ),
]


def detect_platforms(render: RenderResult) -> list[Detection]:
    """The platforms a rendered page loads, one Detection per platform, deduped."""
    urls = " ".join(r.url for r in render.requests).lower()
    html = (render.html or "").lower()

    detections: list[Detection] = []
    for sig in PLATFORMS:
        matched = next((p for p in sig.url_patterns if p in urls), None)
        if matched is None:
            matched = next((p for p in sig.html_patterns if p in html), None)
        if matched is not None:
            detections.append(Detection(sig.key, sig.name, sig.category, matched))
    return detections


def detections_in(detections: list[Detection], category: str) -> list[Detection]:
    return [d for d in detections if d.category == category]
