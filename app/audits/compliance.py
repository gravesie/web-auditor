"""Compliance audit (spec 13.1): UK/EU legal and data side.

Reads the HTTP fetch and the Playwright render. The render is loaded with no
interaction, so cookies and third-party requests seen there fired before any
consent, which is the core signal for the cookies-and-consent category.

Honest scope for this version: cookie/consent, tracker egress, privacy and legal
documentation, forms, and the accessibility headline (axe-core) are assessed from
observable data and rule-based heuristics. Data-transfer jurisdiction uses a
conservative known-US-processor list rather than live IP geolocation, and never
asserts an unlawful transfer (post-2023 Data Privacy Framework). Full per-clause
policy analysis via an LLM is a later enhancement; here completeness is a keyword
scan of the linked policy page.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlsplit

import httpx

from app import scoring
from app.acquisition.domains import registrable_domain
from app.acquisition.fetcher import Acquisition
from app.acquisition.render import RenderResult
from app.audits.base import (
    AuditContext,
    AuditModule,
    AuditResult,
    CategoryDef,
    CategoryResult,
    CheckResult,
)
from app.models.enums import DetectionTag, FindingStatus, Severity

USER_AGENT = "WebAuditor/0.1 (+https://github.com/gravesie/web-auditor)"

_OBS = DetectionTag.observed
_INF = DetectionTag.inferred
_NEEDS = DetectionTag.needs_connection

CATEGORIES = [
    CategoryDef("cookies_consent", "Cookies and consent", 30),
    CategoryDef("trackers_transfers", "Trackers and data transfers", 25),
    CategoryDef("privacy_legal", "Privacy and legal documentation", 20),
    CategoryDef("accessibility", "Accessibility headline", 15),
    CategoryDef("forms", "Forms and data collection", 10),
]
_DEFS = {c.key: c for c in CATEGORIES}

# Registrable domains associated with analytics, advertising and tracking.
_TRACKER_DOMAINS = {
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "googlesyndication.com", "googleadservices.com", "facebook.net",
    "scorecardresearch.com", "quantserve.com", "quantcast.com", "hotjar.com",
    "clarity.ms", "mouseflow.com", "fullstory.com", "segment.com", "segment.io",
    "mixpanel.com", "amplitude.com", "criteo.com", "taboola.com", "outbrain.com",
    "adnxs.com", "ads-twitter.com", "dotmetrics.net", "hubspot.com", "hs-scripts.com",
    "newrelic.com", "nr-data.net", "cloudflareinsights.com",
}

# Confidently US-operated processors, for the transfer flag (not an EU exhaustive list).
_US_PROCESSORS = {
    "google-analytics.com", "googletagmanager.com", "doubleclick.net",
    "googlesyndication.com", "googleadservices.com", "gstatic.com", "facebook.net",
    "facebook.com", "fbcdn.net", "clarity.ms", "hubspot.com", "hs-scripts.com",
    "mixpanel.com", "amplitude.com", "segment.com", "fullstory.com", "newrelic.com",
    "nr-data.net", "scorecardresearch.com", "quantserve.com", "quantcast.com",
    "adnxs.com", "ads-twitter.com",
}

# CMP / cookie-consent signatures to look for in the rendered markup.
_CMP_SIGNATURES = {
    "onetrust": "OneTrust", "cookielaw.org": "OneTrust", "cookiebot": "Cookiebot",
    "consensu.org": "IAB TCF", "quantcast": "Quantcast", "usercentrics": "Usercentrics",
    "osano": "Osano", "cookieyes": "CookieYes", "termly": "Termly", "didomi": "Didomi",
    "trustarc": "TrustArc", "complianz": "Complianz", "cookie-control": "Civic Cookie Control",
    "klaro": "Klaro",
}

_TRACKING_COOKIE_PATTERNS = [
    "_ga", "_gid", "_gat", "_gcl", "_fbp", "_fbc", "_hj", "__utm", "ajs_",
    "_scor", "amplitude", "mp_", "_clck", "_clsk",
]

_LINK_RE = re.compile(r'<a\s[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_FORM_RE = re.compile(r"<form\b[^>]*>.*?</form>", re.I | re.S)
_ACTION_RE = re.compile(r'action=["\']([^"\']*)["\']', re.I)
_PII_INPUT_RE = re.compile(
    r'type=["\'](?:email|tel)["\']'
    r'|name=["\'][^"\']*(?:email|phone|name|address)[^"\']*["\']',
    re.I,
)
_COMPANY_RE = re.compile(
    r"company\s*(?:registration\s*)?(?:no|number)\.?\s*[:#]?\s*\d{6,8}"
    r"|registered\s+in\s+england"
    r"|registered\s+office"
    r"|vat\s*(?:no|number|reg)\.?\s*[:#]?\s*(?:gb)?\s*\d{6,}",
    re.I,
)


def _find_link(html: str, keywords: tuple[str, ...]) -> str | None:
    for href, text in _LINK_RE.findall(html):
        haystack = (href + " " + re.sub(r"<[^>]+>", " ", text)).lower()
        if any(k in haystack for k in keywords):
            return href
    return None


def _detect_cmp(html: str) -> str | None:
    low = html.lower()
    for signature, label in _CMP_SIGNATURES.items():
        if signature in low:
            return label
    return None


def _tracker_hits(render: RenderResult) -> list[str]:
    hits = set()
    for req in render.third_party_requests:
        domain = registrable_domain(urlsplit(req.url).hostname or "")
        if domain in _TRACKER_DOMAINS:
            hits.add(domain)
    return sorted(hits)


def _tracking_cookies(render: RenderResult) -> list[str]:
    names = []
    for cookie in render.cookies:
        low = cookie.name.lower()
        if any(low.startswith(p) or p in low for p in _TRACKING_COOKIE_PATTERNS):
            names.append(cookie.name)
    return names


def _us_processor_hits(render: RenderResult) -> list[str]:
    hits = set()
    for req in render.third_party_requests:
        domain = registrable_domain(urlsplit(req.url).hostname or "")
        if domain in _US_PROCESSORS:
            hits.add(domain)
    return sorted(hits)


def _policy_completeness(url: str) -> tuple[float, list[str]] | None:
    try:
        resp = httpx.get(
            url, follow_redirects=True, timeout=10.0, headers={"User-Agent": USER_AGENT}
        )
    except httpx.HTTPError:
        return None
    if resp.status_code != 200:
        return None
    text = resp.text.lower()
    terms = {
        "lawful basis": ("lawful basis", "legal basis"),
        "data protection / GDPR": ("data protection", "gdpr"),
        "ICO": ("information commissioner", " ico"),
        "retention": ("retention", "retain"),
        "your rights": ("your rights", "right to access", "data subject"),
        "third parties": ("third part", "share your", "disclose"),
        "contact": ("contact us", "data protection officer", "dpo"),
    }
    found = [label for label, keys in terms.items() if any(k in text for k in keys)]
    return len(found) / len(terms), found


class ComplianceAudit(AuditModule):
    key = "compliance"
    label = "Compliance"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        acq: Acquisition = context.data["acquisition"]
        render: RenderResult = context.data["render"]
        base_url = render.final_url or acq.final_url or context.site_domain
        cats = [
            self._cookies_consent(render),
            self._trackers_transfers(render, acq),
            self._privacy_legal(render, base_url),
            self._accessibility(render),
            self._forms(render),
        ]
        return AuditResult(
            audit_key=self.key,
            score=scoring.audit_score(cats, _DEFS),
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    def _cookies_consent(self, render: RenderResult) -> CategoryResult:
        checks: list[CheckResult] = []

        cmp = _detect_cmp(render.html)
        checks.append(
            CheckResult(
                "consent_mechanism",
                100.0 if cmp else 50.0,
                FindingStatus.passed if cmp else FindingStatus.warn,
                Severity.medium,
                _OBS,
                value=f"detected: {cmp}" if cmp else "no consent management platform detected",
                recommendation=None if cmp else "Add a compliant cookie consent mechanism.",
            )
        )

        trackers = _tracker_hits(render)
        cookies = _tracking_cookies(render)
        breach = bool(trackers or cookies)
        detail = []
        if trackers:
            detail.append("trackers: " + ", ".join(trackers))
        if cookies:
            detail.append("cookies: " + ", ".join(cookies[:8]))
        checks.append(
            CheckResult(
                "trackers_before_consent",
                0.0 if breach else 100.0,
                FindingStatus.fail if breach else FindingStatus.passed,
                Severity.high if breach else Severity.low,
                _OBS,
                value=(
                    "non-essential trackers fire before consent (" + "; ".join(detail) + ")"
                    if breach
                    else "no non-essential trackers fire before consent"
                ),
                recommendation=(
                    "Block non-essential tags and cookies until consent is given."
                    if breach
                    else None
                ),
                evidence={"trackers": trackers, "cookies": cookies},
            )
        )
        return CategoryResult("cookies_consent", scoring.category_score(checks), True, checks)

    def _trackers_transfers(self, render: RenderResult, acq: Acquisition) -> CategoryResult:
        checks: list[CheckResult] = []

        third_party = len(render.third_party_requests)
        trackers = _tracker_hits(render)
        checks.append(
            CheckResult(
                "third_party_inventory", None, FindingStatus.info, Severity.info, _OBS,
                value=f"{third_party} third-party requests; {len(trackers)} to known trackers",
                evidence={"trackers": trackers},
            )
        )

        us_hits = _us_processor_hits(render)
        if us_hits:
            checks.append(
                CheckResult(
                    "us_processor_transfer", 60.0, FindingStatus.warn, Severity.medium, _OBS,
                    value="contacts US-associated processors: " + ", ".join(us_hits),
                    recommendation=(
                        "Confirm a transfer basis (e.g. Data Privacy Framework certification) "
                        "and disclose these transfers."
                    ),
                    evidence={"us_processors": us_hits},
                )
            )
        else:
            checks.append(
                CheckResult(
                    "us_processor_transfer", 100.0, FindingStatus.passed, Severity.low, _OBS,
                    value="no known US-associated processors contacted",
                )
            )

        secure = acq.https_enforced
        checks.append(
            CheckResult(
                "secure_transport",
                100.0 if secure else 0.0,
                FindingStatus.passed if secure else FindingStatus.fail,
                Severity.high,
                _OBS,
                value="site served over HTTPS" if secure else "HTTP not redirected to HTTPS",
                recommendation=None if secure else "Serve the whole site over HTTPS.",
            )
        )
        return CategoryResult("trackers_transfers", scoring.category_score(checks), True, checks)

    def _privacy_legal(self, render: RenderResult, base_url: str) -> CategoryResult:
        checks: list[CheckResult] = []
        html = render.html

        privacy_href = _find_link(html, ("privacy",))
        checks.append(
            CheckResult(
                "privacy_policy_present",
                100.0 if privacy_href else 0.0,
                FindingStatus.passed if privacy_href else FindingStatus.fail,
                Severity.high,
                _OBS,
                value="privacy policy linked" if privacy_href else "no privacy policy link found",
                recommendation=None if privacy_href else "Publish and link a privacy policy.",
            )
        )

        if privacy_href:
            checks.append(self._policy_completeness_check(urljoin(base_url, privacy_href)))

        terms_href = _find_link(html, ("terms", "conditions"))
        checks.append(
            CheckResult(
                "terms_present",
                100.0 if terms_href else 50.0,
                FindingStatus.passed if terms_href else FindingStatus.warn,
                Severity.low,
                _OBS,
                value="terms linked" if terms_href else "no terms and conditions link found",
            )
        )

        identity = bool(_COMPANY_RE.search(html))
        checks.append(
            CheckResult(
                "company_identity",
                100.0 if identity else 50.0,
                FindingStatus.passed if identity else FindingStatus.warn,
                Severity.low,
                _INF,
                value=(
                    "company registration / registered office disclosed"
                    if identity
                    else "no company registration details found on the homepage"
                ),
                recommendation=(
                    None if identity else "Show the registered company name, number and office."
                ),
            )
        )
        return CategoryResult("privacy_legal", scoring.category_score(checks), True, checks)

    def _policy_completeness_check(self, url: str) -> CheckResult:
        result = _policy_completeness(url)
        if result is None:
            return CheckResult(
                "privacy_policy_completeness", None, FindingStatus.info, Severity.info, _NEEDS,
                value="could not fetch the privacy policy page to assess it",
            )
        coverage, found = result
        return CheckResult(
            "privacy_policy_completeness",
            round(coverage * 100, 1),
            FindingStatus.passed if coverage >= 0.7 else FindingStatus.warn,
            Severity.medium,
            _INF,
            value=f"covers {len(found)}/7 expected areas: {', '.join(found)}",
            recommendation=(
                None if coverage >= 0.7 else "Expand the privacy policy to cover the missing areas."
            ),
        )

    def _accessibility(self, render: RenderResult) -> CategoryResult:
        checks: list[CheckResult] = []

        if render.axe is None:
            checks.append(
                CheckResult(
                    "wcag_automated", None, FindingStatus.info, Severity.info, _NEEDS,
                    value="automated accessibility scan did not run",
                )
            )
        else:
            violations = render.axe.get("violations", [])
            serious = [v for v in violations if v.get("impact") in ("serious", "critical")]
            count = len(violations)
            if count == 0:
                score, status = 100.0, FindingStatus.passed
            elif len(serious) == 0:
                score, status = 70.0, FindingStatus.warn
            else:
                score, status = max(0.0, 70.0 - 10.0 * len(serious)), FindingStatus.fail
            checks.append(
                CheckResult(
                    "wcag_automated", score, status, Severity.medium, _OBS,
                    value=(
                        f"{count} automated WCAG issue type(s), {len(serious)} serious or critical "
                        "(automated testing catches roughly a third of issues)"
                    ),
                    evidence={"violations": violations[:25]},
                )
            )

        statement = _find_link(render.html, ("accessibility",))
        checks.append(
            CheckResult(
                "accessibility_statement",
                100.0 if statement else 50.0,
                FindingStatus.passed if statement else FindingStatus.warn,
                Severity.low,
                _OBS,
                value=(
                    "accessibility statement linked"
                    if statement
                    else "no accessibility statement link found"
                ),
            )
        )
        return CategoryResult("accessibility", scoring.category_score(checks), True, checks)

    def _forms(self, render: RenderResult) -> CategoryResult:
        html = render.html
        actions: list[str] = []
        pii_forms = 0
        for match in _FORM_RE.finditer(html):
            block = match.group(0)
            if _PII_INPUT_RE.search(block):
                pii_forms += 1
                action = _ACTION_RE.search(block)
                actions.append(action.group(1) if action else "")

        if pii_forms == 0:
            check = CheckResult(
                "pii_forms", 100.0, FindingStatus.passed, Severity.low, _INF,
                value="no personal-data forms detected on the homepage",
            )
            return CategoryResult("forms", scoring.category_score([check]), True, [check])

        checks: list[CheckResult] = [
            CheckResult(
                "pii_forms", None, FindingStatus.info, Severity.info, _INF,
                value=f"{pii_forms} form(s) collecting personal data",
            )
        ]

        insecure = [a for a in actions if a.startswith("http://")]
        checks.append(
            CheckResult(
                "form_secure_submission",
                0.0 if insecure else 100.0,
                FindingStatus.fail if insecure else FindingStatus.passed,
                Severity.high,
                _OBS,
                value=(
                    "a form submits over plain HTTP" if insecure else "forms submit over HTTPS"
                ),
                recommendation="Submit personal data only over HTTPS." if insecure else None,
            )
        )

        notice = _find_link(html, ("privacy",))
        checks.append(
            CheckResult(
                "form_privacy_notice",
                100.0 if notice else 50.0,
                FindingStatus.passed if notice else FindingStatus.warn,
                Severity.medium,
                _INF,
                value=(
                    "a privacy notice is linked near data collection"
                    if notice
                    else "no privacy notice linked alongside the form"
                ),
            )
        )
        return CategoryResult("forms", scoring.category_score(checks), True, checks)
