"""Content strategy audit (spec 13.10).

A site-level, time-series view of the content engine, distinct from the single-page
quality in 13.7. The benchmark: a site that publishes useful content, regularly,
that ranks and drives traffic, and gives that traffic somewhere to convert. Most
sites do none of this.

The scoring rule, agreed for V0-7 and unique to this audit: cadence (category A) and
quality (category B) combine multiplicatively, not by averaging, so quality gates
cadence. A content mill (high volume, low quality) cannot top-score, and neither can
one excellent post a year. The aim point is frequent and genuinely useful. We use a
geometric mean of A and B for that combined headline (worth 60 of 100), then weight
it with content performance (C) and the conversion pathway (D). Geometric mean keeps
the 0-100 scale readable while still gating both ways; it is tunable.

Outside-in limits are real and reported honestly. Cadence dates come from sitemap
lastmod where present (a proxy for publish date). Quality and ICP relevance are an
LLM judgement, degrading to needs-connection without a key. Content performance
needs Search Console or GA4 to answer properly, so it reports needs-connection until
those are wired per content URL.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from urllib.parse import urlsplit

from pydantic import BaseModel

from app import llm, scoring
from app.acquisition.crawler import CrawledPage
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
from app.conversion import detect_conversion_systems, has_cta
from app.models.enums import DetectionTag, FindingStatus, Severity

_OBS = DetectionTag.observed
_INF = DetectionTag.inferred
_NEEDS = DetectionTag.needs_connection

CATEGORIES = [
    CategoryDef("publishing_cadence", "Publishing cadence and consistency", 30),
    CategoryDef("content_quality_icp", "Content quality and ICP relevance", 30),
    CategoryDef("content_performance", "Content performance", 25, conditional=True),
    CategoryDef("conversion_pathway", "Conversion pathway from content", 15),
]
_DEFS = {c.key: c for c in CATEGORIES}

# URL path segments that mark a page as editorial content rather than navigation,
# product or legal pages.
_CONTENT_SEGMENTS = (
    "/blog", "/news", "/insight", "/article", "/guide", "/resource", "/case-stud",
    "/knowledge", "/learn", "/post", "/tips", "/update", "/press", "/stories",
    "/story", "/whitepaper", "/ebook", "/webinar", "/podcast",
)

# How many content titles to put in front of the LLM for the relevance judgement.
_MAX_TITLES_FOR_LLM = 40


class _ContentRelevance(BaseModel):
    relevance_score: int  # 0-100: how well the content set serves the likely ICP
    on_topic: bool
    note: str


def _is_content_url(url: str) -> bool:
    path = urlsplit(url).path.lower()
    return any(seg in path for seg in _CONTENT_SEGMENTS)


def _parse_date(value: str) -> date | None:
    """Tolerant parse of a sitemap lastmod (date or full ISO timestamp)."""
    text = value.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _months_between(earlier: date, later: date) -> float:
    return max((later - earlier).days, 0) / 30.44


class ContentStrategyAudit(AuditModule):
    key = "content_strategy"
    label = "Content strategy"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        acq: Acquisition = context.data["acquisition"]
        render: RenderResult = context.data["render"]
        crawled: list[CrawledPage] = context.data.get("pages", [])
        connectors = context.connectors or {}
        dom = render.html or acq.html

        content_urls = self._content_urls(acq, crawled)
        cadence, a_score = self._cadence(content_urls, acq.sitemap_lastmods)
        quality, b_score = self._quality_icp(content_urls, crawled, context.site_domain)
        performance = self._performance(connectors)
        conversion = self._conversion_pathway(dom)

        cats = [cadence, quality, performance, conversion]
        score = self._headline_score(a_score, b_score, performance.score, conversion.score)
        return AuditResult(
            audit_key=self.key,
            score=score,
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    def _content_urls(self, acq: Acquisition, crawled: list[CrawledPage]) -> list[str]:
        urls = set(acq.sitemap_locs) | {p.url for p in crawled}
        return sorted(u for u in urls if _is_content_url(u))

    # --- A. Publishing cadence and consistency ---
    def _cadence(
        self, content_urls: list[str], lastmods: dict[str, str]
    ) -> tuple[CategoryResult, float | None]:
        checks = [self._inventory(content_urls)]

        dates = sorted(
            d for u in content_urls if (d := _parse_date(lastmods.get(u, ""))) is not None
        )
        if not dates:
            checks.append(
                CheckResult(
                    "content_recency", None, FindingStatus.info, Severity.info, _NEEDS,
                    value="no publish dates available (sitemap has no lastmod)",
                    recommendation="Add lastmod dates to the sitemap so freshness can be measured.",
                )
            )
        elif len(set(dates)) == 1:
            # A single shared date across all content usually means a bulk export, not a
            # real publishing rhythm; report it but don't infer a cadence from it.
            checks.append(
                CheckResult(
                    "content_recency", None, FindingStatus.info, Severity.info, _INF,
                    value=f"all sitemap dates are identical ({dates[0]}); likely auto-generated",
                )
            )
        else:
            checks.append(self._recency(dates))
            checks.append(self._regularity(dates))

        score = scoring.category_score(checks)
        return CategoryResult("publishing_cadence", score, True, checks), score

    def _inventory(self, content_urls: list[str]) -> CheckResult:
        count = len(content_urls)
        if count == 0:
            score, status, severity = 0.0, FindingStatus.fail, Severity.high
            value = "no content pages found (no blog, news, insights or similar)"
            rec = "Start publishing content that answers your buyers' questions."
        elif count < 5:
            score, status, severity = 45.0, FindingStatus.warn, Severity.medium
            value = f"only {count} content page(s) found"
            rec = "Build out a content library; a handful of pages is not a strategy."
        elif count < 15:
            score, status, severity = 70.0, FindingStatus.warn, Severity.low
            value = f"{count} content pages found"
            rec = "Keep publishing to deepen topic coverage."
        elif count < 50:
            score, status, severity = 90.0, FindingStatus.passed, Severity.low
            value = f"{count} content pages found"
            rec = None
        else:
            score, status, severity = 100.0, FindingStatus.passed, Severity.low
            value = f"{count} content pages found"
            rec = None
        return CheckResult(
            "content_inventory", score, status, severity, _OBS, value=value, recommendation=rec
        )

    def _recency(self, dates: list[date]) -> CheckResult:
        newest = dates[-1]
        months = _months_between(newest, datetime.now(UTC).date())
        if months <= 1:
            score, status, severity = 100.0, FindingStatus.passed, Severity.low
        elif months <= 3:
            score, status, severity = 85.0, FindingStatus.passed, Severity.low
        elif months <= 6:
            score, status, severity = 70.0, FindingStatus.warn, Severity.low
        elif months <= 12:
            score, status, severity = 55.0, FindingStatus.warn, Severity.medium
        elif months <= 24:
            score, status, severity = 35.0, FindingStatus.warn, Severity.medium
        else:
            score, status, severity = 15.0, FindingStatus.fail, Severity.high
        return CheckResult(
            "content_recency", score, status, severity, _OBS,
            value=f"newest content dated {newest} ({months:.0f} months ago)",
            recommendation=(
                None if months <= 3 else "Publish more recently; the engine looks stalled."
            ),
        )

    def _regularity(self, dates: list[date]) -> CheckResult:
        span_months = max(_months_between(dates[0], dates[-1]), 1.0)
        per_month = len(dates) / span_months
        if per_month >= 20:
            score, status, label = 100.0, FindingStatus.passed, "around daily"
        elif per_month >= 4:
            score, status, label = 85.0, FindingStatus.passed, "around weekly"
        elif per_month >= 1:
            score, status, label = 70.0, FindingStatus.warn, "around monthly"
        elif per_month >= 0.33:
            score, status, label = 45.0, FindingStatus.warn, "roughly quarterly"
        else:
            score, status, label = 25.0, FindingStatus.warn, "sporadic"
        return CheckResult(
            "publishing_regularity", score, status, Severity.low, _INF,
            value=f"publishing rate {per_month:.1f}/month ({label})",
            recommendation=(
                None if per_month >= 4 else "Publish on a steadier, more frequent cadence."
            ),
        )

    # --- B. Content quality and ICP relevance ---
    def _quality_icp(
        self, content_urls: list[str], crawled: list[CrawledPage], domain: str
    ) -> tuple[CategoryResult, float | None]:
        if not content_urls:
            check = CheckResult(
                "content_relevance", None, FindingStatus.info, Severity.info, _NEEDS,
                value="no content to assess for quality or relevance",
            )
            return CategoryResult("content_quality_icp", None, True, [check]), None

        titles = self._content_titles(content_urls, crawled)
        if not llm.available():
            check = CheckResult(
                "content_relevance", None, FindingStatus.info, Severity.info, _NEEDS,
                value="content relevance needs an LLM assessment (no API key configured)",
                recommendation="Connect the LLM to judge whether content serves your buyers.",
            )
            return CategoryResult("content_quality_icp", None, True, [check]), None

        verdict = llm.judge(
            "You assess whether a website's content programme serves its likely ideal "
            "customer. Judge the topic set as a whole: is it focused on the buyer's "
            "questions and journey, or scattered and off-target. Score relevance 0-100.",
            f"Site: {domain}\nContent page titles:\n" + "\n".join(f"- {t}" for t in titles),
            _ContentRelevance,
        )
        if verdict is None:
            check = CheckResult(
                "content_relevance", None, FindingStatus.info, Severity.info, _NEEDS,
                value="content relevance could not be assessed this run",
            )
            return CategoryResult("content_quality_icp", None, True, [check]), None

        score = float(max(0, min(100, verdict.relevance_score)))
        status = FindingStatus.passed if score >= 70 else FindingStatus.warn
        check = CheckResult(
            "content_relevance", score, status, Severity.medium, _INF,
            value=verdict.note,
            recommendation=None if score >= 70 else "Refocus content on your buyers' questions.",
            evidence={"on_topic": verdict.on_topic, "titles_sampled": len(titles)},
        )
        return CategoryResult("content_quality_icp", score, True, [check]), score

    def _content_titles(self, content_urls: list[str], crawled: list[CrawledPage]) -> list[str]:
        by_url = {p.url: p for p in crawled}
        titles: list[str] = []
        for url in content_urls:
            page = by_url.get(url)
            if page and page.title:
                titles.append(page.title)
            else:
                # Fall back to a readable slug when the page wasn't crawled.
                slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
                if slug:
                    titles.append(slug.replace("-", " ").replace("_", " "))
        return titles[:_MAX_TITLES_FOR_LLM]

    # --- C. Content performance ---
    def _performance(self, connectors: dict) -> CategoryResult:
        # Answering this properly needs per-content-URL Search Console / GA4 data, which
        # isn't wired yet. Report needs-connection so the category rebalances out rather
        # than guessing. (Conditional category.)
        connected = "gsc" in connectors or "ga4" in connectors
        value = (
            "Search Console / GA4 connected, but per-content-URL performance isn't wired yet"
            if connected
            else "content performance needs Search Console or GA4 to measure"
        )
        check = CheckResult(
            "content_performance", None, FindingStatus.info, Severity.info, _NEEDS,
            value=value,
            recommendation="Connect Search Console and GA4 to see which content earns traffic.",
        )
        return CategoryResult("content_performance", None, False, [check])

    # --- D. Conversion pathway from content ---
    def _conversion_pathway(self, dom: str) -> CategoryResult:
        systems = detect_conversion_systems(dom)
        cta = has_cta(dom)
        if systems and cta:
            score, status = 100.0, FindingStatus.passed
            value = "conversion routes and a clear CTA are present: " + ", ".join(systems)
            rec = None
        elif systems:
            score, status = 70.0, FindingStatus.warn
            value = "conversion routes present (" + ", ".join(systems) + ") but no clear CTA"
            rec = "Add a clear call to action so content readers know the next step."
        else:
            score, status = 30.0, FindingStatus.fail
            value = "no visible conversion route, so content that ranks dead-ends"
            rec = "Give content a next step: a CTA, a relevant offer, or a link to convert."
        check = CheckResult(
            "conversion_pathway", score, status, Severity.medium, _INF,
            value=value, recommendation=rec,
        )
        return CategoryResult("conversion_pathway", score, True, [check])

    # --- Headline scoring: A and B multiplicatively, then weight with C and D ---
    def _headline_score(
        self, a: float | None, b: float | None, c: float | None, d: float | None
    ) -> float | None:
        if a is not None and b is not None:
            ab = math.sqrt(a * b)  # geometric mean: quality gates cadence, both ways
        else:
            ab = a if a is not None else b  # one half missing: fall back to the other

        blocks: list[tuple[float, float]] = []
        if ab is not None:
            blocks.append((ab, 60.0))
        if c is not None:
            blocks.append((c, 25.0))
        if d is not None:
            blocks.append((d, 15.0))
        total = sum(w for _, w in blocks)
        if not total:
            return None
        return sum(s * w for s, w in blocks) / total
