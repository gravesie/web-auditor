"""On-page SEO audit (spec 13.4): is each page optimised for what it targets.

The on-page elements (titles, meta, headings, images) are assessed on the homepage
render, and title/meta population and uniqueness across the crawled page set.
Keyword targeting and semantic depth are light rule-based proxies for now; the
deeper intent and coverage analysis wants an LLM and is flagged as such. Ranking
performance needs Search Console or a SERP source and reports as needs-connection,
with the anticipated target phrases shown as the outside-in best effort.
"""

from __future__ import annotations

import re

from app import scoring
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
from app.models.enums import DetectionTag, FindingStatus, Severity

_OBS = DetectionTag.observed
_INF = DetectionTag.inferred
_NEEDS = DetectionTag.needs_connection

CATEGORIES = [
    CategoryDef("on_page_elements", "On-page elements", 25),
    CategoryDef("keyword_targeting", "Keyword targeting and intent", 20),
    CategoryDef("semantic_content", "Semantic and content optimisation", 20),
    CategoryDef("ranking_performance", "Keyword and ranking performance", 25),
    CategoryDef("local_seo", "Local SEO", 10, conditional=True),
]
_DEFS = {c.key: c for c in CATEGORIES}

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_IMG_RE = re.compile(r"<img\s[^>]*?>", re.I)
_ALT_RE = re.compile(r'alt=["\']([^"\']*)["\']', re.I)
_TAG_RE = re.compile(r"<[^>]+>")
_META_TAG_RE = re.compile(r"<meta\s[^>]*?>", re.I)
_NAME_DESC_RE = re.compile(r'name=["\']description["\']', re.I)
_CONTENT_RE = re.compile(r'content=["\']([^"\']*)["\']', re.I)
_TEL_RE = re.compile(r'href=["\']tel:', re.I)
_STOPWORDS = {"the", "and", "for", "with", "your", "you", "our", "are", "from", "this", "that"}


def _meta_description(html: str) -> str | None:
    for tag in _META_TAG_RE.findall(html):
        if _NAME_DESC_RE.search(tag):
            match = _CONTENT_RE.search(tag)
            return match.group(1).strip() if match else None
    return None


def _text(html_fragment: str) -> str:
    return _TAG_RE.sub(" ", html_fragment).strip()


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


def _score_length(value: int, low: int, high: int) -> tuple[float, FindingStatus]:
    if low <= value <= high:
        return 100.0, FindingStatus.passed
    return 60.0, FindingStatus.warn


def _ranking_checks_from_gsc(gsc: dict) -> list[CheckResult]:
    """Scored ranking checks from live Search Console data (observed)."""
    clicks = int(gsc.get("total_clicks", 0) or 0)
    impressions = int(gsc.get("total_impressions", 0) or 0)
    avg_position = gsc.get("avg_position")
    striking = gsc.get("striking_distance") or []

    checks: list[CheckResult] = []

    # Search visibility: is the site appearing in results, and earning clicks?
    if clicks > 0:
        checks.append(
            CheckResult(
                "search_visibility", 100.0, FindingStatus.passed, Severity.low, _OBS,
                value=f"{clicks} clicks from {impressions} impressions (last 28 days)",
            )
        )
    elif impressions > 0:
        checks.append(
            CheckResult(
                "search_visibility", 60.0, FindingStatus.warn, Severity.medium, _OBS,
                value=f"{impressions} impressions but 0 clicks (last 28 days)",
                recommendation="The site appears in search but earns no clicks. Improve "
                "titles and meta descriptions to lift click-through.",
            )
        )
    else:
        checks.append(
            CheckResult(
                "search_visibility", 25.0, FindingStatus.fail, Severity.high, _OBS,
                value="no impressions in search over the last 28 days",
                recommendation="The site is not appearing in Google search. Check "
                "indexation and that pages target real demand.",
            )
        )

    # Average position, impression-weighted. Absent when there were no impressions.
    if avg_position is not None and impressions > 0:
        if avg_position <= 5:
            score, status, severity = 100.0, FindingStatus.passed, Severity.low
        elif avg_position <= 10:
            score, status, severity = 80.0, FindingStatus.passed, Severity.low
        elif avg_position <= 20:
            score, status, severity = 55.0, FindingStatus.warn, Severity.medium
        else:
            score, status, severity = 30.0, FindingStatus.fail, Severity.medium
        checks.append(
            CheckResult(
                "average_position", score, status, severity, _OBS,
                value=f"average position {avg_position:.1f} (impression-weighted, last 28 days)",
            )
        )

    # Striking-distance opportunities: ranking on page 1-2 but not the top. Reported
    # as an opportunity list rather than scored — it is upside, not a fault.
    if striking:
        terms = ", ".join(q.get("query", "") for q in striking[:5])
        checks.append(
            CheckResult(
                "striking_distance", None, FindingStatus.info, Severity.info, _OBS,
                value=f"{len(striking)} queries ranking 3-20 (quick wins): {terms}",
                recommendation="Strengthen pages targeting these terms to push them onto "
                "page one.",
                evidence={"striking_distance": striking[:10]},
            )
        )

    return checks


class OnPageSeoAudit(AuditModule):
    key = "on_page_seo"
    label = "On-page SEO"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        acq: Acquisition = context.data["acquisition"]
        render: RenderResult = context.data["render"]
        pages: list[CrawledPage] = context.data.get("pages", [])
        dom = render.html or acq.html

        cats = [
            self._on_page_elements(dom, pages),
            self._keyword_targeting(dom),
            self._semantic_content(dom),
            self._ranking_performance(dom, context.connectors.get("search_console")),
            self._local_seo(dom),
        ]
        return AuditResult(
            audit_key=self.key,
            score=scoring.audit_score(cats, _DEFS),
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    def _on_page_elements(self, dom: str, pages: list[CrawledPage]) -> CategoryResult:
        checks: list[CheckResult] = []

        title_match = _TITLE_RE.search(dom)
        title = _text(title_match.group(1)) if title_match else ""
        if not title:
            checks.append(
                CheckResult(
                    "title_tag", 0.0, FindingStatus.fail, Severity.high, _OBS,
                    value="no title tag", recommendation="Add a descriptive title tag.",
                )
            )
        else:
            score, status = _score_length(len(title), 30, 60)
            checks.append(
                CheckResult(
                    "title_tag", score, status, Severity.medium, _OBS,
                    value=f'title "{title[:60]}" ({len(title)} chars)',
                    recommendation=(
                        None if status == FindingStatus.passed else "Aim for 30-60 characters."
                    ),
                )
            )

        meta = _meta_description(dom)
        if not meta:
            checks.append(
                CheckResult(
                    "meta_description", 50.0, FindingStatus.warn, Severity.medium, _OBS,
                    value="no meta description",
                    recommendation="Add a meta description (120-160 characters).",
                )
            )
        else:
            score, status = _score_length(len(meta), 120, 160)
            checks.append(
                CheckResult(
                    "meta_description", score, status, Severity.low, _OBS,
                    value=f"meta description ({len(meta)} chars)",
                    recommendation=(
                        None if status == FindingStatus.passed else "Aim for 120-160 characters."
                    ),
                )
            )

        h1s = [_text(h) for h in _H1_RE.findall(dom)]
        h1s = [h for h in h1s if h]
        if len(h1s) == 1:
            checks.append(
                CheckResult(
                    "h1", 100.0, FindingStatus.passed, Severity.low, _OBS,
                    value=f'single H1: "{h1s[0][:60]}"',
                )
            )
        else:
            checks.append(
                CheckResult(
                    "h1", 50.0, FindingStatus.warn, Severity.medium, _OBS,
                    value=f"{len(h1s)} H1 headings (expected exactly one)",
                    recommendation="Use exactly one H1 per page.",
                )
            )

        checks.append(self._image_alt(dom))
        checks.append(self._title_uniqueness(pages))
        return CategoryResult("on_page_elements", scoring.category_score(checks), True, checks)

    def _image_alt(self, dom: str) -> CheckResult:
        imgs = _IMG_RE.findall(dom)
        if not imgs:
            return CheckResult(
                "image_alt", None, FindingStatus.info, Severity.info, _OBS,
                value="no images on the homepage",
            )
        with_alt = 0
        for tag in imgs:
            alt = _ALT_RE.search(tag)
            if alt and alt.group(1).strip():
                with_alt += 1
        pct = with_alt / len(imgs) * 100
        return CheckResult(
            "image_alt",
            pct,
            FindingStatus.passed if pct >= 90 else FindingStatus.warn,
            Severity.low,
            _OBS,
            value=f"{with_alt}/{len(imgs)} images have alt text",
            recommendation="Add descriptive alt text to images." if pct < 90 else None,
        )

    def _title_uniqueness(self, pages: list[CrawledPage]) -> CheckResult:
        titles = [p.title.strip() for p in pages if p.title and p.title.strip()]
        if len(titles) < 2:
            return CheckResult(
                "title_uniqueness", None, FindingStatus.info, Severity.info, _OBS,
                value="too few crawled titles to compare",
            )
        unique = len(set(titles))
        dupes = len(titles) - unique
        return CheckResult(
            "title_uniqueness",
            100.0 if dupes == 0 else max(0.0, 100.0 * unique / len(titles)),
            FindingStatus.passed if dupes == 0 else FindingStatus.warn,
            Severity.medium,
            _OBS,
            value=f"{unique} unique titles across {len(titles)} crawled pages",
            recommendation="Give each page a unique title." if dupes else None,
        )

    def _keyword_targeting(self, dom: str) -> CategoryResult:
        title_match = _TITLE_RE.search(dom)
        title = _text(title_match.group(1)) if title_match else ""
        h1_match = _H1_RE.search(dom)
        h1 = _text(h1_match.group(1)) if h1_match else ""

        if not title or not h1:
            check = CheckResult(
                "title_h1_alignment", 50.0, FindingStatus.warn, Severity.medium, _INF,
                value="missing title or H1, so the page target is unclear",
                recommendation="Give the page a clear title and H1.",
            )
        else:
            overlap = _keywords(title) & _keywords(h1)
            aligned = bool(overlap)
            check = CheckResult(
                "title_h1_alignment",
                100.0 if aligned else 60.0,
                FindingStatus.passed if aligned else FindingStatus.warn,
                Severity.low,
                _INF,
                value=(
                    "title and H1 share keywords: " + ", ".join(sorted(overlap))
                    if aligned
                    else "title and H1 do not share keywords"
                ),
                recommendation=None if aligned else "Align the title and H1 on the page's topic.",
            )

        note = CheckResult(
            "intent_analysis", None, FindingStatus.info, Severity.info, _NEEDS,
            value="deeper intent and cannibalisation analysis needs an LLM pass (later)",
        )
        checks = [check, note]
        return CategoryResult("keyword_targeting", scoring.category_score(checks), True, checks)

    def _semantic_content(self, dom: str) -> CategoryResult:
        words = len(re.findall(r"[a-zA-Z]{2,}", _text(dom)))
        if words >= 300:
            score, status, value = 100.0, FindingStatus.passed, f"~{words} words of content"
        elif words >= 120:
            score, status, value = 70.0, FindingStatus.warn, f"~{words} words (thin)"
        else:
            score, status, value = 40.0, FindingStatus.warn, f"~{words} words (very thin)"
        depth = CheckResult(
            "content_depth", score, status, Severity.medium, _INF, value=value,
            recommendation=(
                None if words >= 300 else "Add substantive content for the page's topic."
            ),
        )
        note = CheckResult(
            "semantic_coverage", None, FindingStatus.info, Severity.info, _NEEDS,
            value="topical coverage versus competitors needs an LLM pass (later)",
        )
        checks = [depth, note]
        return CategoryResult("semantic_content", scoring.category_score(checks), True, checks)

    def _ranking_performance(self, dom: str, gsc: dict | None) -> CategoryResult:
        title_match = _TITLE_RE.search(dom)
        title = _text(title_match.group(1)) if title_match else ""
        phrases = sorted(_keywords(title))[:8]
        anticipated = CheckResult(
            "anticipated_phrases", None, FindingStatus.info, Severity.info, _INF,
            value="likely target terms from on-page signals: " + (", ".join(phrases) or "none"),
        )

        if gsc is None:
            rankings = CheckResult(
                "rankings", None, FindingStatus.info, Severity.info, _NEEDS,
                value="actual rankings and striking-distance need Search Console or a SERP source",
            )
            # No scorable check yet, so the category reports as not-yet-assessed.
            checks = [anticipated, rankings]
            return CategoryResult(
                "ranking_performance", scoring.category_score(checks), True, checks
            )

        checks = [anticipated, *_ranking_checks_from_gsc(gsc)]
        return CategoryResult("ranking_performance", scoring.category_score(checks), True, checks)

    def _local_seo(self, dom: str) -> CategoryResult:
        low = dom.lower()
        has_local = bool(_TEL_RE.search(dom)) or "localbusiness" in low or "postaladdress" in low
        if not has_local:
            check = CheckResult(
                "local_signals", None, FindingStatus.info, Severity.info, _OBS,
                value="no local-business signals; not treated as a local site",
            )
            return CategoryResult("local_seo", None, False, [check])

        has_tel = bool(_TEL_RE.search(dom))
        check = CheckResult(
            "nap_presence",
            100.0 if has_tel else 60.0,
            FindingStatus.passed if has_tel else FindingStatus.warn,
            Severity.low,
            _INF,
            value="contact phone present" if has_tel else "local signals but no tel link found",
            recommendation=None if has_tel else "Show a consistent name, address and phone.",
        )
        return CategoryResult("local_seo", scoring.category_score([check]), True, [check])
