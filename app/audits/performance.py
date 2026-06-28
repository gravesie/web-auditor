"""Performance and Core Web Vitals audit (spec 13.5).

Reads PageSpeed Insights (Lighthouse lab data and CrUX field data). Field data is
real-user and is what Google ranks on, so it carries the weight; where a site has
no field data we fall back to lab proxies and flag them as estimates. If PageSpeed
could not be reached, the whole audit reports as not-assessed (excluded from the
site score) rather than scoring zero.

Currently assesses the homepage on the mobile strategy (mobile-first). Desktop and
a per-template sample are later additions.
"""

from __future__ import annotations

from app import scoring
from app.acquisition.fetcher import Acquisition
from app.acquisition.pagespeed import PageSpeedResult
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
    CategoryDef("core_web_vitals", "Core Web Vitals", 40),
    CategoryDef("lab_performance", "Lab performance", 25),
    CategoryDef("page_weight", "Page weight and resources", 20),
    CategoryDef("delivery", "Delivery and caching", 15),
]
_DEFS = {c.key: c for c in CATEGORIES}


def _band(value: float, good: float, ok: float) -> tuple[float, FindingStatus]:
    if value <= good:
        return 100.0, FindingStatus.passed
    if value <= ok:
        return 60.0, FindingStatus.warn
    return 25.0, FindingStatus.fail


class PerformanceAudit(AuditModule):
    key = "performance"
    label = "Performance and Core Web Vitals"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        acq: Acquisition = context.data["acquisition"]
        psi: PageSpeedResult | None = context.data.get("pagespeed")

        if psi is None or not psi.ok:
            reason = psi.error if psi else "PageSpeed Insights was not run"
            note = CheckResult(
                "pagespeed_unavailable", None, FindingStatus.info, Severity.info, _NEEDS,
                value=f"performance not assessed: {reason}",
            )
            category = CategoryResult("core_web_vitals", None, True, [note])
            return AuditResult(self.key, None, 0.0, [category])

        cats = [
            self._core_web_vitals(psi),
            self._lab_performance(psi),
            self._page_weight(psi),
            self._delivery(acq),
        ]
        return AuditResult(
            audit_key=self.key,
            score=scoring.audit_score(cats, _DEFS),
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    def _core_web_vitals(self, psi: PageSpeedResult) -> CategoryResult:
        checks = [
            self._metric(psi, "lcp", "LCP (loading)", 2500, 4000, "ms"),
            self._metric(psi, "inp", "INP (interactivity)", 200, 500, "ms", lab_fallback="tbt"),
            self._metric(psi, "cls", "CLS (visual stability)", 0.1, 0.25, ""),
        ]
        return CategoryResult("core_web_vitals", scoring.category_score(checks), True, checks)

    def _metric(
        self,
        psi: PageSpeedResult,
        key: str,
        label: str,
        good: float,
        ok: float,
        unit: str,
        lab_fallback: str | None = None,
    ) -> CheckResult:
        field_metric = psi.field.get(key)
        if field_metric is not None:
            value, detection, source = field_metric.value, _OBS, "field"
        else:
            value, detection, source = psi.lab.get(lab_fallback or key), _INF, "lab estimate"

        if value is None:
            return CheckResult(
                f"cwv_{key}", None, FindingStatus.info, Severity.info, _NEEDS,
                value=f"{label}: no data",
            )
        score, status = _band(value, good, ok)
        shown = f"{value:.2f}" if key == "cls" else f"{value:.0f}{unit}"
        return CheckResult(
            f"cwv_{key}", score, status, Severity.high, detection,
            value=f"{label}: {shown} ({source})",
            recommendation=None if status == FindingStatus.passed else f"Improve {label}.",
        )

    def _lab_performance(self, psi: PageSpeedResult) -> CategoryResult:
        checks: list[CheckResult] = []
        if psi.lighthouse_score is not None:
            score = psi.lighthouse_score
            status = (
                FindingStatus.passed
                if score >= 90
                else FindingStatus.warn
                if score >= 50
                else FindingStatus.fail
            )
            checks.append(
                CheckResult(
                    "lighthouse_score", score, status, Severity.medium, _OBS,
                    value=f"Lighthouse performance {score:.0f}/100",
                )
            )
        ttfb = psi.lab.get("ttfb")
        if ttfb is not None:
            score, status = _band(ttfb, 800, 1800)
            checks.append(
                CheckResult(
                    "ttfb", score, status, Severity.medium, _OBS,
                    value=f"server response (TTFB) {ttfb:.0f}ms",
                    recommendation=(
                        None if status == FindingStatus.passed else "Reduce server response time."
                    ),
                )
            )
        return CategoryResult("lab_performance", scoring.category_score(checks), True, checks)

    def _page_weight(self, psi: PageSpeedResult) -> CategoryResult:
        total = psi.total_bytes
        if total is None:
            check = CheckResult(
                "page_weight", None, FindingStatus.info, Severity.info, _OBS,
                value="no page-weight data",
            )
        else:
            mb = total / 1_048_576
            if total <= 1_572_864:
                score, status = 100.0, FindingStatus.passed
            elif total <= 3_145_728:
                score, status = 60.0, FindingStatus.warn
            else:
                score, status = 30.0, FindingStatus.fail
            check = CheckResult(
                "page_weight", score, status, Severity.low, _OBS,
                value=f"total page weight {mb:.1f} MB",
                recommendation=(
                    None
                    if status == FindingStatus.passed
                    else "Reduce page weight (images, scripts)."
                ),
            )
        return CategoryResult("page_weight", scoring.category_score([check]), True, [check])

    def _delivery(self, acq: Acquisition) -> CategoryResult:
        encoding = acq.headers.get("content-encoding", "")
        compressed = "gzip" in encoding or "br" in encoding
        check = CheckResult(
            "compression",
            100.0 if compressed else 50.0,
            FindingStatus.passed if compressed else FindingStatus.warn,
            Severity.low,
            _OBS,
            value=(
                f"response compressed ({encoding})"
                if compressed
                else "homepage response is not compressed"
            ),
            recommendation=None if compressed else "Enable gzip or brotli compression.",
        )
        return CategoryResult("delivery", scoring.category_score([check]), True, [check])
