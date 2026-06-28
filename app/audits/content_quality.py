"""Content quality and E-E-A-T audit (spec 13.7).

There is no "E-E-A-T score" Google publishes; this assesses observable signals of
experience, expertise, authoritativeness and trust, rule-based for now. The deeper
quality judgement (is this genuinely expert, original content) wants an LLM pass
and is flagged as such rather than faked.

Assessed mostly on the homepage render plus cross-page signals from the crawl.
Overlap with on-page SEO is deliberate but framed differently: on-page SEO judges
optimisation, this judges whether the content earns trust.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

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
    CategoryDef("depth_substance", "Depth and substance", 25),
    CategoryDef("authoritativeness_trust", "Authoritativeness and trust", 25),
    CategoryDef("expertise_experience", "Expertise and experience signals", 20),
    CategoryDef("freshness", "Freshness and maintenance", 15),
    CategoryDef("readability_structure", "Readability and structure", 15),
]
_DEFS = {c.key: c for c in CATEGORIES}

_TAG_RE = re.compile(r"<[^>]+>")
_LINK_RE = re.compile(r'<a\s[^>]*?href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.I | re.S)
_HEADING_RE = re.compile(r"<h[2-6][^>]*>", re.I)
_LIST_ITEM_RE = re.compile(r"<li\b", re.I)
_AUTHOR_RE = re.compile(r'\bby\s+[A-Z][a-z]+|rel=["\']author["\']|written by|author', re.I)
_DATE_RE = re.compile(
    r"\b(20\d{2})[-/](0?[1-9]|1[0-2])[-/](0?[1-9]|[12]\d|3[01])\b"
    r"|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2},?\s+20\d{2}",
    re.I,
)
_UPDATED_RE = re.compile(r"last updated|last modified|published|updated on", re.I)
_COPYRIGHT_RE = re.compile(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", re.I)
_SOCIAL_PROOF_RE = re.compile(
    r"testimonial|review|trustpilot|rated|rating|\bstars?\b|case study|what our", re.I
)
_SOCIAL_LINK_RE = re.compile(r"facebook\.com|twitter\.com|linkedin\.com|instagram\.com", re.I)
_ADDRESS_RE = re.compile(
    r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b"  # UK postcode
    r"|registered office|our address|find us",
    re.I,
)
_AI_PROSE_RE = [
    re.compile(p, re.I)
    for p in (
        r"in today's (?:digital |fast-paced )?world",
        r"unlock the (?:power|potential)",
        r"elevate your",
        r"in conclusion",
        r"when it comes to",
        r"a wide range of",
    )
]


def _find_link(html: str, keywords: tuple[str, ...]) -> bool:
    for href, text in _LINK_RE.findall(html):
        haystack = (href + " " + _TAG_RE.sub(" ", text)).lower()
        if any(k in haystack for k in keywords):
            return True
    return False


class ContentQualityAudit(AuditModule):
    key = "content_quality"
    label = "Content quality and E-E-A-T"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        acq: Acquisition = context.data["acquisition"]
        render: RenderResult = context.data["render"]
        pages: list[CrawledPage] = context.data.get("pages", [])
        dom = render.html or acq.html
        text = _TAG_RE.sub(" ", dom)

        cats = [
            self._depth_substance(dom, text, pages),
            self._authoritativeness_trust(dom, text),
            self._expertise_experience(dom),
            self._freshness(text),
            self._readability_structure(dom, text),
        ]
        return AuditResult(
            audit_key=self.key,
            score=scoring.audit_score(cats, _DEFS),
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    def _depth_substance(
        self, dom: str, text: str, pages: list[CrawledPage]
    ) -> CategoryResult:
        words = len(re.findall(r"[a-zA-Z]{2,}", text))
        if words >= 500:
            score, status, value = 100.0, FindingStatus.passed, f"~{words} words on the homepage"
        elif words >= 200:
            score, status, value = 70.0, FindingStatus.warn, f"~{words} words (light)"
        else:
            score, status, value = 40.0, FindingStatus.warn, f"~{words} words (thin)"
        substance = CheckResult(
            "substance", score, status, Severity.medium, _INF, value=value,
            recommendation=None if words >= 500 else "Add substantive, original content.",
        )

        titles = [p.title.strip() for p in pages if p.title and p.title.strip()]
        if len(titles) >= 2:
            dupes = len(titles) - len(set(titles))
            duplicate = CheckResult(
                "duplicate_content",
                100.0 if dupes == 0 else 50.0,
                FindingStatus.passed if dupes == 0 else FindingStatus.warn,
                Severity.medium,
                _OBS,
                value=(
                    "no duplicate page titles across the crawl"
                    if dupes == 0
                    else f"{dupes} duplicate page title(s) suggest templated or thin content"
                ),
                recommendation="Give each page distinct, substantive content." if dupes else None,
            )
        else:
            duplicate = CheckResult(
                "duplicate_content", None, FindingStatus.info, Severity.info, _OBS,
                value="too few crawled pages to assess duplication",
            )
        note = CheckResult(
            "depth_analysis", None, FindingStatus.info, Severity.info, _NEEDS,
            value="genuine depth and originality versus competitors needs an LLM pass (later)",
        )
        checks = [substance, duplicate, note]
        return CategoryResult("depth_substance", scoring.category_score(checks), True, checks)

    def _authoritativeness_trust(self, dom: str, text: str) -> CategoryResult:
        about = _find_link(dom, ("about",))
        contact = _find_link(dom, ("contact",))
        trust_pages = about and contact
        has_address = bool(_ADDRESS_RE.search(text))
        has_proof = bool(_SOCIAL_PROOF_RE.search(text))
        checks = [
            CheckResult(
                "trust_pages",
                100.0 if trust_pages else (60.0 if about or contact else 20.0),
                FindingStatus.passed if trust_pages else FindingStatus.warn,
                Severity.medium,
                _OBS,
                value=f"about: {'yes' if about else 'no'}, contact: {'yes' if contact else 'no'}",
                recommendation=None if trust_pages else "Provide clear About and Contact pages.",
            ),
            CheckResult(
                "physical_presence",
                100.0 if has_address else 50.0,
                FindingStatus.passed if has_address else FindingStatus.warn,
                Severity.low,
                _INF,
                value=(
                    "address or location details present"
                    if has_address
                    else "no address or location details found"
                ),
            ),
            CheckResult(
                "social_proof",
                100.0 if has_proof else 60.0,
                FindingStatus.passed if has_proof else FindingStatus.warn,
                Severity.low,
                _INF,
                value=(
                    "social proof (reviews/testimonials) present"
                    if has_proof
                    else "no obvious reviews or testimonials"
                ),
                recommendation=(
                    None if has_proof else "Add reviews, testimonials or case studies."
                ),
            ),
        ]
        return CategoryResult(
            "authoritativeness_trust", scoring.category_score(checks), True, checks
        )

    def _expertise_experience(self, dom: str) -> CategoryResult:
        has_author = bool(_AUTHOR_RE.search(dom))
        author = CheckResult(
            "authorship",
            100.0 if has_author else 50.0,
            FindingStatus.passed if has_author else FindingStatus.warn,
            Severity.low,
            _INF,
            value=(
                "author / byline signals present" if has_author else "no author or byline signals"
            ),
            recommendation=None if has_author else "Attribute content to named authors with bios.",
        )
        note = CheckResult(
            "first_hand_experience", None, FindingStatus.info, Severity.info, _NEEDS,
            value="first-hand experience and expertise depth need an LLM pass (later)",
        )
        checks = [author, note]
        return CategoryResult("expertise_experience", scoring.category_score(checks), True, checks)

    def _freshness(self, text: str) -> CategoryResult:
        has_dates = bool(_DATE_RE.search(text)) or bool(_UPDATED_RE.search(text))
        checks = [
            CheckResult(
                "content_dates",
                100.0 if has_dates else 50.0,
                FindingStatus.passed if has_dates else FindingStatus.warn,
                Severity.low,
                _INF,
                value=(
                    "dated or recently-updated content"
                    if has_dates
                    else "no visible content dates"
                ),
                recommendation=None if has_dates else "Show publish or last-updated dates.",
            )
        ]
        years = [int(y) for y in _COPYRIGHT_RE.findall(text)]
        if years:
            latest = max(years)
            stale = latest < datetime.now(UTC).year - 1
            checks.append(
                CheckResult(
                    "copyright_current",
                    50.0 if stale else 100.0,
                    FindingStatus.warn if stale else FindingStatus.passed,
                    Severity.low,
                    _OBS,
                    value=f"footer year {latest}",
                    recommendation="Update the footer copyright year." if stale else None,
                )
            )
        return CategoryResult("freshness", scoring.category_score(checks), True, checks)

    def _readability_structure(self, dom: str, text: str) -> CategoryResult:
        headings = len(_HEADING_RE.findall(dom))
        lists = len(_LIST_ITEM_RE.findall(dom))
        scannable = headings >= 2 or lists >= 3
        structure = CheckResult(
            "scannability",
            100.0 if scannable else 50.0,
            FindingStatus.passed if scannable else FindingStatus.warn,
            Severity.low,
            _OBS,
            value=f"{headings} sub-headings, {lists} list items",
            recommendation=None if scannable else "Break content up with headings and lists.",
        )

        ai_hits = sum(1 for pattern in _AI_PROSE_RE if pattern.search(text))
        prose = CheckResult(
            "generic_prose",
            100.0 if ai_hits == 0 else max(50.0, 100.0 - 15.0 * ai_hits),
            FindingStatus.passed if ai_hits == 0 else FindingStatus.warn,
            Severity.low,
            _INF,
            value=(
                "no obvious generic / AI-template phrasing"
                if ai_hits == 0
                else f"{ai_hits} generic / AI-template phrase pattern(s)"
            ),
            recommendation="Replace generic phrasing with specifics." if ai_hits else None,
        )
        checks = [structure, prose]
        return CategoryResult("readability_structure", scoring.category_score(checks), True, checks)
