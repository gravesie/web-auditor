"""Analytics and measurement audit (spec 13.11).

Whether the business can see what its site is doing. We detect the measurement
tools installed, from the rendered page and its network requests, and name what's
missing. Detection runs through the shared tag-fingerprint module (app/fingerprint),
which also serves compliance and the build fingerprint.

Honest scope, stated in the spec: on-site tools load scripts, so they're
detectable; external research suites (SimilarWeb, SEMrush, Ahrefs) leave no trace
on the audited site, so we never claim to detect them. Where our own Google
connector is present, that confirms analytics installation directly.

Each category is one layer of the measurement stack. A missing layer is scored down
and turned into a recommendation with its commercial consequence, the absence of
web analytics most of all: no analytics means no measurement.
"""

from __future__ import annotations

from app import scoring
from app.acquisition.render import RenderResult
from app.audits.base import (
    AuditContext,
    AuditModule,
    AuditResult,
    CategoryDef,
    CategoryResult,
    CheckResult,
)
from app.fingerprint import (
    ANALYTICS,
    BEHAVIOURAL,
    CONVERSION,
    INFRA,
    detect_platforms,
    detections_in,
)
from app.models.enums import DetectionTag, FindingStatus, Severity

_OBS = DetectionTag.observed

CATEGORIES = [
    CategoryDef("web_analytics_presence", "Web analytics presence", 35),
    CategoryDef("conversion_campaign", "Conversion and campaign measurement", 30),
    CategoryDef("behavioural_product", "Behavioural and product analytics", 20),
    CategoryDef("data_infra_governance", "Data infrastructure and governance", 15),
]
_DEFS = {c.key: c for c in CATEGORIES}


class AnalyticsAudit(AuditModule):
    key = "analytics"
    label = "Analytics and measurement"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        render: RenderResult = context.data["render"]
        connectors = context.connectors or {}

        # Without a usable render we can't observe what loads; report not-assessed
        # rather than wrongly claiming nothing is installed.
        if not render.html and not render.requests:
            cats = [CategoryResult(c.key, None, True, [_unobserved(c.key)]) for c in CATEGORIES]
            return AuditResult(
                audit_key=self.key,
                score=None,
                completeness=0.0,
                categories=cats,
            )

        detections = detect_platforms(render)
        cats = [
            self._web_analytics(detections, connectors),
            self._conversion(detections),
            self._behavioural(detections),
            self._infra(detections),
        ]
        return AuditResult(
            audit_key=self.key,
            score=scoring.audit_score(cats, _DEFS),
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    def _web_analytics(self, detections, connectors) -> CategoryResult:
        found = detections_in(detections, ANALYTICS)
        names = [d.name for d in found]
        # Our own GA4 connector confirms analytics directly, even if a tag manager
        # hides the on-page signature.
        if "ga4" in connectors and "Google Analytics 4" not in names:
            names.append("Google Analytics 4 (connected)")

        present = bool(names)
        check = CheckResult(
            "web_analytics",
            100.0 if present else 0.0,
            FindingStatus.passed if present else FindingStatus.fail,
            Severity.low if present else Severity.high,
            _OBS,
            value=(
                "web analytics: " + ", ".join(names) if present else "no web analytics detected"
            ),
            recommendation=(
                None
                if present
                else "Install GA4 (or an alternative); with no analytics, traffic and "
                "conversions are invisible."
            ),
            evidence={"detected": [d.key for d in found]},
        )
        score = scoring.category_score([check])
        return CategoryResult("web_analytics_presence", score, True, [check])

    def _conversion(self, detections) -> CategoryResult:
        found = detections_in(detections, CONVERSION)
        names = [d.name for d in found]
        present = bool(names)
        check = CheckResult(
            "conversion_tracking",
            100.0 if present else 50.0,
            FindingStatus.passed if present else FindingStatus.warn,
            Severity.low if present else Severity.medium,
            _OBS,
            value=(
                "conversion and ad tracking: " + ", ".join(names)
                if present
                else "no conversion or ad-platform tracking detected"
            ),
            recommendation=(
                None
                if present
                else "Add conversion tracking for the channels you spend on, so you can "
                "tie traffic to outcomes."
            ),
            evidence={"detected": [d.key for d in found]},
        )
        return CategoryResult("conversion_campaign", scoring.category_score([check]), True, [check])

    def _behavioural(self, detections) -> CategoryResult:
        found = detections_in(detections, BEHAVIOURAL)
        names = [d.name for d in found]
        present = bool(names)
        check = CheckResult(
            "behavioural_analytics",
            100.0 if present else 60.0,
            FindingStatus.passed if present else FindingStatus.warn,
            Severity.low,
            _OBS,
            value=(
                "behavioural tools: " + ", ".join(names)
                if present
                else "no heatmap, session-replay or product analytics detected"
            ),
            recommendation=(
                None
                if present
                else "Add a behavioural tool (Hotjar or Clarity) to see how visitors "
                "actually use the site."
            ),
            evidence={"detected": [d.key for d in found]},
        )
        return CategoryResult("behavioural_product", scoring.category_score([check]), True, [check])

    def _infra(self, detections) -> CategoryResult:
        found = detections_in(detections, INFRA)
        names = [d.name for d in found]
        present = bool(names)
        check = CheckResult(
            "data_infrastructure",
            100.0 if present else 65.0,
            FindingStatus.passed if present else FindingStatus.warn,
            Severity.low,
            _OBS,
            value=(
                "tag and data infrastructure: " + ", ".join(names)
                if present
                else "no tag manager or customer-data platform detected"
            ),
            recommendation=(
                None
                if present
                else "Use a tag manager (GTM) to organise and govern your measurement tags."
            ),
            evidence={"detected": [d.key for d in found]},
        )
        score = scoring.category_score([check])
        return CategoryResult("data_infra_governance", score, True, [check])


def _unobserved(category_key: str) -> CheckResult:
    return CheckResult(
        "measurement_unobserved",
        None,
        FindingStatus.info,
        Severity.info,
        DetectionTag.needs_connection,
        value="the page could not be rendered, so installed tools can't be detected",
    )
