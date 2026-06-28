"""Technical SEO audit (spec 13.3): can engines reach, crawl and index the site.

Reads the HTTP fetch (robots, sitemap, headers), the crawl (page set, click depth,
status codes) and the homepage render (canonical, meta robots, hreflang, viewport).
Schema is scored separately (structured data audit) and only referenced here.

Per-page indexation signals (canonical, noindex) are assessed on the homepage only
for now, since rendering every page is expensive; the indexed-page count needs
Search Console and reports as needs-connection rather than relying on `site:`.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app import scoring
from app.acquisition.crawler import CrawledPage
from app.acquisition.fetcher import Acquisition, SitemapProbe
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

_OBS = DetectionTag.observed
_INF = DetectionTag.inferred
_NEEDS = DetectionTag.needs_connection

CATEGORIES = [
    CategoryDef("indexation", "Indexation", 30),
    CategoryDef("crawlability", "Crawlability and access", 25),
    CategoryDef("site_response", "Site response and redirects", 20),
    CategoryDef("mobile", "Mobile and rendering readiness", 15),
    CategoryDef("international", "International (hreflang)", 10, conditional=True),
]
_DEFS = {c.key: c for c in CATEGORIES}

_A_HREF_RE = re.compile(r"<a\s[^>]*?href=", re.I)
_LINK_TAG_RE = re.compile(r"<link\s[^>]*?>", re.I)
_META_TAG_RE = re.compile(r"<meta\s[^>]*?>", re.I)
_HREF_ATTR_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
_CONTENT_ATTR_RE = re.compile(r'content=["\']([^"\']*)["\']', re.I)
_HREFLANG_RE = re.compile(r'<link\s[^>]*hreflang=["\']([^"\']+)["\']', re.I)


def _meta_content(dom: str, name: str) -> str | None:
    """Content of a <meta name="..."> tag, regardless of attribute order. None if absent."""
    name_re = re.compile(rf'name=["\']{name}["\']', re.I)
    for tag in _META_TAG_RE.findall(dom):
        if name_re.search(tag):
            match = _CONTENT_ATTR_RE.search(tag)
            return match.group(1) if match else ""
    return None


def _canonical_href(dom: str) -> str | None:
    """href of the <link rel="canonical"> tag, regardless of attribute order."""
    for tag in _LINK_TAG_RE.findall(dom):
        if re.search(r'rel=["\']canonical["\']', tag, re.I):
            match = _HREF_ATTR_RE.search(tag)
            if match:
                return match.group(1)
    return None


def _blocks_everything(robots_txt: str) -> bool:
    """True if robots.txt disallows the whole site for all agents."""
    agent_all = False
    for line in robots_txt.splitlines():
        low = line.strip().lower()
        if low.startswith("user-agent:"):
            agent_all = low.split(":", 1)[1].strip() == "*"
        elif agent_all and low.startswith("disallow:"):
            if low.split(":", 1)[1].strip() == "/":
                return True
    return False


def _normalise_url(url: str) -> str:
    """Reduce a URL to host + path (+ query) for set comparison.

    Scheme is dropped so http/https don't count as different pages, and a trailing
    slash is removed so /about and /about/ match. The fragment is ignored.
    """
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return url.strip().lower()
    host = (parts.hostname or "").lower()
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    base = f"{host}{path}"
    return f"{base}?{parts.query}" if parts.query else base


def _sitemap_coverage(sitemap_locs: list[str], pages: list[CrawledPage]) -> CheckResult | None:
    """Crawled pages that the sitemap leaves out (the sitemap is stale or incomplete).

    Measured against the pages the crawl actually reached, which is a bounded set, so
    this reports coverage of discovered pages rather than the whole site.
    """
    if not sitemap_locs:
        return None
    in_sitemap = {_normalise_url(u) for u in sitemap_locs}
    crawled = {_normalise_url(p.url) for p in pages if p.status == 200 and p.url}
    if not crawled:
        return None

    missing = sorted(crawled - in_sitemap)
    covered = len(crawled) - len(missing)
    ratio = covered / len(crawled)
    if ratio >= 0.9:
        score, status, severity = 100.0, FindingStatus.passed, Severity.low
    elif ratio >= 0.6:
        score, status, severity = 70.0, FindingStatus.warn, Severity.medium
    else:
        score, status, severity = 40.0, FindingStatus.warn, Severity.medium

    return CheckResult(
        "sitemap_coverage",
        score,
        status,
        severity,
        _OBS,
        value=f"{covered} of {len(crawled)} crawled pages are listed in the sitemap",
        recommendation=(
            None
            if not missing
            else "List reachable pages in the sitemap, or drop them if they are non-canonical."
        ),
        evidence={"missing_from_sitemap": missing[:10]} if missing else {},
    )


def _sitemap_health(sample: list[SitemapProbe]) -> CheckResult | None:
    """Dead or redirecting entries in a sampled set of sitemap URLs."""
    if not sample:
        return None
    dead = [p for p in sample if p.status is None or p.status >= 400]
    redirecting = [p for p in sample if p.redirected and p.status is not None and p.status < 400]
    healthy = len(sample) - len(dead)
    score = healthy / len(sample) * 100

    if dead:
        status = FindingStatus.fail if score < 80 else FindingStatus.warn
        severity = Severity.medium
    elif redirecting:
        status, severity = FindingStatus.warn, Severity.low
    else:
        status, severity = FindingStatus.passed, Severity.low

    parts = []
    if dead:
        parts.append(f"{len(dead)} dead")
    if redirecting:
        parts.append(f"{len(redirecting)} redirecting")
    detail = ", ".join(parts) if parts else "all reachable"
    recommendation = None
    if dead:
        recommendation = "Remove dead URLs from the sitemap; it should list live, canonical pages."
    elif redirecting:
        recommendation = "List the final canonical URLs in the sitemap, not ones that redirect."

    return CheckResult(
        "sitemap_health",
        score,
        status,
        severity,
        _OBS,
        value=f"sampled {len(sample)} sitemap URLs: {detail}",
        recommendation=recommendation,
        evidence={
            "dead": [p.url for p in dead[:10]],
            "redirecting": [p.url for p in redirecting[:10]],
        },
    )


class TechnicalSeoAudit(AuditModule):
    key = "technical_seo"
    label = "Technical SEO"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        acq: Acquisition = context.data["acquisition"]
        render: RenderResult = context.data["render"]
        pages: list[CrawledPage] = context.data.get("pages", [])
        dom = render.html or acq.html

        cats = [
            self._indexation(acq, dom),
            self._crawlability(acq, dom, pages),
            self._site_response(pages),
            self._mobile(dom),
            self._international(dom),
        ]
        return AuditResult(
            audit_key=self.key,
            score=scoring.audit_score(cats, _DEFS),
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    def _indexation(self, acq: Acquisition, dom: str) -> CategoryResult:
        checks: list[CheckResult] = []

        robots_content = _meta_content(dom, "robots")
        noindex = bool(robots_content and "noindex" in robots_content.lower())
        checks.append(
            CheckResult(
                "homepage_indexable",
                0.0 if noindex else 100.0,
                FindingStatus.fail if noindex else FindingStatus.passed,
                Severity.critical if noindex else Severity.low,
                _OBS,
                value="homepage is set to noindex" if noindex else "homepage is indexable",
                recommendation="Remove noindex from the homepage." if noindex else None,
            )
        )

        canonical = _canonical_href(dom)
        checks.append(
            CheckResult(
                "canonical_tag",
                100.0 if canonical else 50.0,
                FindingStatus.passed if canonical else FindingStatus.warn,
                Severity.medium,
                _OBS,
                value=f"canonical: {canonical}" if canonical else "no canonical tag found",
                recommendation=None if canonical else "Add a self-referencing canonical tag.",
            )
        )

        secure = acq.https_enforced
        checks.append(
            CheckResult(
                "https_canonicalisation",
                100.0 if secure else 0.0,
                FindingStatus.passed if secure else FindingStatus.fail,
                Severity.medium,
                _OBS,
                value=(
                    "HTTP canonicalises to HTTPS" if secure else "HTTP does not redirect to HTTPS"
                ),
                recommendation=None if secure else "Redirect HTTP to the HTTPS canonical.",
            )
        )

        checks.append(
            CheckResult(
                "indexed_count", None, FindingStatus.info, Severity.info, _NEEDS,
                value="real indexed-page count needs Search Console (site: is unreliable)",
            )
        )
        return CategoryResult("indexation", scoring.category_score(checks), True, checks)

    def _crawlability(
        self, acq: Acquisition, dom: str, pages: list[CrawledPage]
    ) -> CategoryResult:
        checks: list[CheckResult] = []

        if acq.robots_txt is None:
            checks.append(
                CheckResult(
                    "robots_txt", 50.0, FindingStatus.warn, Severity.low, _OBS,
                    value="no robots.txt found",
                    recommendation="Add a robots.txt.",
                )
            )
        elif _blocks_everything(acq.robots_txt):
            checks.append(
                CheckResult(
                    "robots_txt", 0.0, FindingStatus.fail, Severity.critical, _OBS,
                    value="robots.txt disallows the entire site",
                    recommendation="Remove the site-wide Disallow.",
                )
            )
        else:
            checks.append(
                CheckResult(
                    "robots_txt", 100.0, FindingStatus.passed, Severity.low, _OBS,
                    value="robots.txt present and not blocking the site",
                )
            )

        locs = len(acq.sitemap_locs)
        sitemap_value = (
            f"sitemap with {locs} URLs{' (index)' if acq.sitemap_is_index else ''}"
            if locs
            else "no usable XML sitemap found"
        )
        checks.append(
            CheckResult(
                "xml_sitemap",
                100.0 if locs else 50.0,
                FindingStatus.passed if locs else FindingStatus.warn,
                Severity.medium,
                _OBS,
                value=sitemap_value,
                recommendation=(
                    None if locs else "Publish an XML sitemap and reference it in robots.txt."
                ),
            )
        )

        checks.append(self._crawl_depth(pages))
        checks.append(self._js_dependency(acq.html, dom))
        for extra in (
            _sitemap_coverage(acq.sitemap_locs, pages),
            _sitemap_health(acq.sitemap_sample),
        ):
            if extra is not None:
                checks.append(extra)
        return CategoryResult("crawlability", scoring.category_score(checks), True, checks)

    def _crawl_depth(self, pages: list[CrawledPage]) -> CheckResult:
        depths = [p.depth for p in pages if p.depth is not None]
        if not depths:
            return CheckResult(
                "crawl_depth", None, FindingStatus.info, Severity.info, _OBS,
                value="no crawl data",
            )
        deep = sum(1 for d in depths if d >= 4)
        if deep == 0:
            score, status = 100.0, FindingStatus.passed
        elif deep <= 3:
            score, status = 70.0, FindingStatus.warn
        else:
            score, status = 40.0, FindingStatus.warn
        return CheckResult(
            "crawl_depth", score, status, Severity.low, _OBS,
            value=f"max depth {max(depths)}, {deep} page(s) four or more clicks deep",
            recommendation="Flatten deep pages closer to the homepage." if deep else None,
        )

    def _js_dependency(self, raw_html: str, rendered_html: str) -> CheckResult:
        raw_links = len(_A_HREF_RE.findall(raw_html))
        rendered_links = len(_A_HREF_RE.findall(rendered_html))
        js_reliant = rendered_links > raw_links * 1.5 and (rendered_links - raw_links) > 10
        return CheckResult(
            "js_content_dependency",
            50.0 if js_reliant else 100.0,
            FindingStatus.warn if js_reliant else FindingStatus.passed,
            Severity.medium,
            _INF,
            value=(
                f"links jump from {raw_links} (raw HTML) to {rendered_links} (rendered); "
                "much content depends on JavaScript"
                if js_reliant
                else f"content largely present in raw HTML ({raw_links} vs {rendered_links} links)"
            ),
            recommendation=(
                "Ensure key content and links are server-rendered." if js_reliant else None
            ),
        )

    def _site_response(self, pages: list[CrawledPage]) -> CategoryResult:
        with_status = [p for p in pages if p.status is not None]
        if not with_status:
            check = CheckResult(
                "status_health", None, FindingStatus.info, Severity.info, _OBS,
                value="no crawl status data",
            )
            return CategoryResult("site_response", None, True, [check])

        total = len(with_status)
        ok = sum(1 for p in with_status if 200 <= p.status < 300)
        errors = [p for p in with_status if p.status >= 400]
        score = ok / total * 100
        check = CheckResult(
            "status_health",
            score,
            FindingStatus.passed if not errors else FindingStatus.warn,
            Severity.medium if errors else Severity.low,
            _OBS,
            value=f"{ok}/{total} crawled pages returned 2xx; {len(errors)} returned 4xx/5xx",
            recommendation="Fix or redirect the error pages." if errors else None,
            evidence={"errors": [p.url for p in errors[:20]]},
        )
        return CategoryResult("site_response", scoring.category_score([check]), True, [check])

    def _mobile(self, dom: str) -> CategoryResult:
        has_viewport = _meta_content(dom, "viewport") is not None
        check = CheckResult(
            "viewport_meta",
            100.0 if has_viewport else 0.0,
            FindingStatus.passed if has_viewport else FindingStatus.fail,
            Severity.high,
            _OBS,
            value="responsive viewport meta present" if has_viewport else "no viewport meta tag",
            recommendation=None if has_viewport else "Add a responsive viewport meta tag.",
        )
        return CategoryResult("mobile", scoring.category_score([check]), True, [check])

    def _international(self, dom: str) -> CategoryResult:
        langs = _HREFLANG_RE.findall(dom)
        if not langs:
            # Not a multi-region site as far as we can see; category does not apply.
            check = CheckResult(
                "hreflang", None, FindingStatus.info, Severity.info, _OBS,
                value="no hreflang tags; treated as a single-locale site",
            )
            return CategoryResult("international", None, False, [check])

        has_default = any(lang.lower() == "x-default" for lang in langs)
        check = CheckResult(
            "hreflang",
            100.0 if has_default else 70.0,
            FindingStatus.passed if has_default else FindingStatus.warn,
            Severity.medium,
            _OBS,
            value=f"{len(set(langs))} hreflang locales" + ("" if has_default else "; no x-default"),
            recommendation=None if has_default else "Add an x-default hreflang entry.",
            evidence={"locales": sorted(set(langs))},
        )
        return CategoryResult("international", scoring.category_score([check]), True, [check])
