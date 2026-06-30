"""Messaging and positioning clarity audit (spec 13.9).

Scores the message on the page: who it's for, what it does, why choose it, and
whether the next step is obvious. The most subjective of the audits; the genuine
clarity-of-positioning judgement wants an LLM and is flagged as such. The
observable parts (CTAs, conversion systems, generic-filler density, proof and
audience signals) are assessed rule-based here.

Assessed on the homepage render, with brand consistency across the crawled titles.
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
from app.conversion import detect_conversion_systems, has_cta
from app.models.enums import DetectionTag, FindingStatus, Severity

_OBS = DetectionTag.observed
_INF = DetectionTag.inferred
_NEEDS = DetectionTag.needs_connection

CATEGORIES = [
    CategoryDef("clarity", "Clarity of what it does", 25),
    CategoryDef("value_prop", "Value proposition and differentiation", 25),
    CategoryDef("conversion", "Conversion path and CTAs", 20),
    CategoryDef("audience", "Audience and ICP signalling", 20),
    CategoryDef("consistency", "Messaging consistency", 10),
]
_DEFS = {c.key: c for c in CATEGORIES}

_TAG_RE = re.compile(r"<[^>]+>")
_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)

_FILLER = [
    "innovative solutions", "world-class", "world class", "leading provider",
    "cutting-edge", "cutting edge", "best-in-class", "industry-leading",
    "one-stop shop", "trusted partner", "state-of-the-art", "next-generation",
    "seamless", "synergy", "empower", "unlock your", "take it to the next level",
]
_BENEFIT_RE = re.compile(
    r"\b(grow|save|increase|reduce|boost|faster|results|revenue|profit|convert|"
    r"leads|roi|win|scale|improve)\b",
    re.I,
)
_AUDIENCE_RE = re.compile(
    r"\bfor (?:businesses|teams|brands|agencies|founders|marketers|enterprises|smbs?)\b"
    r"|designed for|built for|we help|helping|ideal for|our clients|for companies",
    re.I,
)
_PROOF_RE = re.compile(
    r"trusted by|\bclients?\b|\bcustomers?\b|testimonial|case stud|rated|\b\d+%|"
    r"\b\d+\+?\s*(?:clients|customers|companies|years)",
    re.I,
)


def _text(html: str) -> str:
    return _TAG_RE.sub(" ", html)


class MessagingAudit(AuditModule):
    key = "messaging"
    label = "Messaging and positioning clarity"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        acq: Acquisition = context.data["acquisition"]
        render: RenderResult = context.data["render"]
        pages: list[CrawledPage] = context.data.get("pages", [])
        dom = render.html or acq.html
        text = _text(dom)

        cats = [
            self._clarity(dom, text),
            self._value_prop(text),
            self._conversion(dom, text),
            self._audience(text),
            self._consistency(render, acq, pages),
        ]
        return AuditResult(
            audit_key=self.key,
            score=scoring.audit_score(cats, _DEFS),
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    def _clarity(self, dom: str, text: str) -> CategoryResult:
        h1_match = _H1_RE.search(dom)
        h1 = _TAG_RE.sub(" ", h1_match.group(1)).strip() if h1_match else ""
        hero = CheckResult(
            "hero_headline",
            100.0 if h1 else 40.0,
            FindingStatus.passed if h1 else FindingStatus.fail,
            Severity.medium,
            _OBS,
            value=f'headline: "{h1[:70]}"' if h1 else "no H1 headline to anchor the message",
            recommendation=None if h1 else "Lead with a clear headline saying what you do.",
        )

        low = text.lower()
        fillers = [p for p in _FILLER if p in low]
        vagueness = CheckResult(
            "plain_language",
            100.0 if not fillers else max(40.0, 100.0 - 15.0 * len(fillers)),
            FindingStatus.passed if not fillers else FindingStatus.warn,
            Severity.low,
            _INF,
            value=(
                "no generic filler phrases detected"
                if not fillers
                else f"{len(fillers)} generic filler phrase(s): " + ", ".join(fillers[:4])
            ),
            recommendation="Replace generic phrasing with concrete specifics." if fillers else None,
        )

        note = CheckResult(
            "clarity_judgement", None, FindingStatus.info, Severity.info, _NEEDS,
            value="the five-second 'what is this' judgement needs an LLM pass (later)",
        )
        checks = [hero, vagueness, note]
        return CategoryResult("clarity", scoring.category_score(checks), True, checks)

    def _value_prop(self, text: str) -> CategoryResult:
        benefit = bool(_BENEFIT_RE.search(text))
        proof = bool(_PROOF_RE.search(text))
        checks = [
            CheckResult(
                "benefit_language",
                100.0 if benefit else 50.0,
                FindingStatus.passed if benefit else FindingStatus.warn,
                Severity.medium,
                _INF,
                value=(
                    "outcome / benefit language present"
                    if benefit
                    else "little outcome-focused language (mostly features?)"
                ),
                recommendation=(
                    None if benefit else "Frame the value as outcomes, not just features."
                ),
            ),
            CheckResult(
                "proof",
                100.0 if proof else 50.0,
                FindingStatus.passed if proof else FindingStatus.warn,
                Severity.low,
                _INF,
                value=(
                    "proof signals present (clients, numbers, testimonials)"
                    if proof
                    else "no proof signals"
                ),
                recommendation=(
                    None if proof else "Back claims with proof: numbers, clients, results."
                ),
            ),
        ]
        return CategoryResult("value_prop", scoring.category_score(checks), True, checks)

    def _conversion(self, dom: str, text: str) -> CategoryResult:
        cta = has_cta(text)
        cta_check = CheckResult(
            "cta_present",
            100.0 if cta else 30.0,
            FindingStatus.passed if cta else FindingStatus.fail,
            Severity.high,
            _OBS,
            value="clear call-to-action present" if cta else "no obvious call-to-action",
            recommendation=None if cta else "Add a clear primary call-to-action.",
        )

        systems = detect_conversion_systems(dom)
        systems_check = CheckResult(
            "conversion_systems",
            100.0 if systems else 40.0,
            FindingStatus.passed if systems else FindingStatus.warn,
            Severity.medium,
            _OBS,
            value=(
                "conversion routes: " + ", ".join(systems)
                if systems
                else "no visible conversion routes"
            ),
            recommendation=(
                None if systems else "Offer a clear way to convert (form, booking, contact)."
            ),
        )
        checks = [cta_check, systems_check]
        return CategoryResult("conversion", scoring.category_score(checks), True, checks)

    def _audience(self, text: str) -> CategoryResult:
        signalled = bool(_AUDIENCE_RE.search(text))
        check = CheckResult(
            "audience_signal",
            100.0 if signalled else 50.0,
            FindingStatus.passed if signalled else FindingStatus.warn,
            Severity.medium,
            _INF,
            value=(
                "states who it is for"
                if signalled
                else "does not clearly state who it is for"
            ),
            recommendation=None if signalled else "Say explicitly who the site is for.",
        )
        note = CheckResult(
            "icp_fit", None, FindingStatus.info, Severity.info, _NEEDS,
            value="whether the message fits the real ICP is the synthesis layer plus analytics",
        )
        checks = [check, note]
        return CategoryResult("audience", scoring.category_score(checks), True, checks)

    def _consistency(
        self, render: RenderResult, acq: Acquisition, pages: list[CrawledPage]
    ) -> CategoryResult:
        brand = (render.title or "").split("|")[-1].split("-")[-1].strip().lower()
        titles = [p.title.lower() for p in pages if p.title]
        if not brand or len(titles) < 3:
            check = CheckResult(
                "brand_consistency", None, FindingStatus.info, Severity.info, _OBS,
                value="too little data to assess brand consistency across pages",
            )
            return CategoryResult("consistency", None, True, [check])

        with_brand = sum(1 for t in titles if brand and brand in t)
        ratio = with_brand / len(titles)
        check = CheckResult(
            "brand_consistency",
            100.0 if ratio >= 0.6 else 60.0,
            FindingStatus.passed if ratio >= 0.6 else FindingStatus.warn,
            Severity.low,
            _OBS,
            value=f'brand "{brand}" in {with_brand}/{len(titles)} page titles',
            recommendation=(
                None if ratio >= 0.6 else "Keep brand and message consistent across pages."
            ),
        )
        return CategoryResult("consistency", scoring.category_score([check]), True, [check])
