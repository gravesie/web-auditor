"""Structured data and schema audit (spec 13.6).

Reads the rendered DOM (not raw HTML), because schema is often injected by
JavaScript; parsing the raw HTML alone would wrongly report "no schema". JSON-LD
is the primary target; microdata and RDFa are detected for presence.

Honest flag baked in: Google removed FAQ and HowTo rich results for most sites in
2023, so those are reported as valid markup that no longer earns a rich result,
rather than promising a snippet that will not appear.
"""

from __future__ import annotations

import json
import re

from app import scoring
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

CATEGORIES = [
    CategoryDef("presence", "Presence and coverage", 30),
    CategoryDef("validity", "Validity and correctness", 30),
    CategoryDef("rich_results", "Rich result eligibility", 20),
    CategoryDef("entity", "Entity and knowledge graph signals", 20),
]
_DEFS = {c.key: c for c in CATEGORIES}

_JSONLD_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S
)

# Types that can still earn a rich result.
_RICH_TYPES = {
    "Product", "Recipe", "Event", "BreadcrumbList", "Review", "AggregateRating",
    "VideoObject", "Article", "NewsArticle", "BlogPosting", "JobPosting", "Course",
    "SoftwareApplication", "Book", "LocalBusiness", "Organization",
}
# Valid markup, but no longer shown as a rich result for most sites.
_DEPRECATED_RICH = {"FAQPage", "HowTo"}
_ORG_TYPES = {
    "Organization", "LocalBusiness", "Corporation", "NGO", "NewsMediaOrganization",
    "EducationalOrganization", "GovernmentOrganization", "OnlineBusiness", "OnlineStore",
    "Store", "ProfessionalService",
}


def _parse_jsonld(html: str) -> tuple[list, int]:
    """Return parsed JSON-LD objects and a count of blocks that failed to parse."""
    blocks: list = []
    invalid = 0
    for raw in _JSONLD_RE.findall(html):
        try:
            blocks.append(json.loads(raw.strip()))
        except json.JSONDecodeError:
            invalid += 1
    return blocks, invalid


def _walk(obj, visit) -> None:
    if isinstance(obj, dict):
        visit(obj)
        for value in obj.values():
            _walk(value, visit)
    elif isinstance(obj, list):
        for item in obj:
            _walk(item, visit)


def _collect_types(blocks: list) -> set[str]:
    types: set[str] = set()

    def visit(node: dict) -> None:
        raw = node.get("@type")
        for value in raw if isinstance(raw, list) else [raw]:
            if isinstance(value, str):
                types.add(value)

    for block in blocks:
        _walk(block, visit)
    return types


def _find_objects(blocks: list, wanted: set[str]) -> list[dict]:
    found: list[dict] = []

    def visit(node: dict) -> None:
        raw = node.get("@type")
        for value in raw if isinstance(raw, list) else [raw]:
            if isinstance(value, str) and value in wanted:
                found.append(node)
                return

    for block in blocks:
        _walk(block, visit)
    return found


class SchemaAudit(AuditModule):
    key = "schema"
    label = "Structured data and schema"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        acq: Acquisition = context.data["acquisition"]
        render: RenderResult = context.data["render"]
        dom = render.html or acq.html

        blocks, invalid = _parse_jsonld(dom)
        types = _collect_types(blocks)
        has_microdata = "itemscope" in dom.lower()
        has_rdfa = "typeof=" in dom.lower() or "vocab=" in dom.lower()

        cats = [
            self._presence(blocks, types, has_microdata, has_rdfa),
            self._validity(blocks, invalid, types),
            self._rich_results(types),
            self._entity(blocks),
        ]
        return AuditResult(
            audit_key=self.key,
            score=scoring.audit_score(cats, _DEFS),
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    def _presence(
        self, blocks: list, types: set[str], microdata: bool, rdfa: bool
    ) -> CategoryResult:
        checks: list[CheckResult] = []
        any_structured = bool(blocks) or microdata or rdfa

        formats = []
        if blocks:
            formats.append(f"JSON-LD ({len(blocks)} block(s))")
        if microdata:
            formats.append("microdata")
        if rdfa:
            formats.append("RDFa")
        checks.append(
            CheckResult(
                "structured_data_present",
                100.0 if any_structured else 0.0,
                FindingStatus.passed if any_structured else FindingStatus.fail,
                Severity.medium,
                _OBS,
                value=", ".join(formats) if any_structured else "no structured data found",
                recommendation=None if any_structured else "Add JSON-LD structured data.",
            )
        )

        core = (_ORG_TYPES | {"WebSite"}) & types
        checks.append(
            CheckResult(
                "core_types",
                100.0 if core else 50.0,
                FindingStatus.passed if core else FindingStatus.warn,
                Severity.low,
                _INF,
                value=(
                    "foundational types present: " + ", ".join(sorted(core))
                    if core
                    else "no Organization/LocalBusiness or WebSite markup"
                ),
                recommendation=None if core else "Add Organization and WebSite markup.",
                evidence={"types": sorted(types)},
            )
        )
        return CategoryResult("presence", scoring.category_score(checks), True, checks)

    def _validity(self, blocks: list, invalid: int, types: set[str]) -> CategoryResult:
        if not blocks and invalid == 0:
            check = CheckResult(
                "json_ld_valid", None, FindingStatus.info, Severity.info, _OBS,
                value="no JSON-LD to validate",
            )
            return CategoryResult("validity", None, True, [check])

        total = len(blocks) + invalid
        checks = [
            CheckResult(
                "json_ld_valid",
                100.0 if invalid == 0 else max(0.0, 100.0 * len(blocks) / total),
                FindingStatus.passed if invalid == 0 else FindingStatus.fail,
                Severity.medium,
                _OBS,
                value=(
                    f"all {len(blocks)} JSON-LD block(s) parse"
                    if invalid == 0
                    else f"{invalid} of {total} JSON-LD block(s) are invalid JSON"
                ),
                recommendation="Fix the malformed JSON-LD." if invalid else None,
            )
        ]

        # Light required-property check on common types.
        missing = self._missing_required(blocks, types)
        checks.append(
            CheckResult(
                "required_properties",
                100.0 if not missing else 60.0,
                FindingStatus.passed if not missing else FindingStatus.warn,
                Severity.low,
                _INF,
                value=(
                    "key required properties present"
                    if not missing
                    else "missing recommended properties: " + "; ".join(missing)
                ),
                recommendation=None if not missing else "Add the missing properties.",
            )
        )
        return CategoryResult("validity", scoring.category_score(checks), True, checks)

    def _missing_required(self, blocks: list, types: set[str]) -> list[str]:
        required = {
            "Organization": ["name"],
            "LocalBusiness": ["name", "address"],
            "Article": ["headline"],
            "BlogPosting": ["headline"],
            "Product": ["name"],
            "Event": ["name", "startDate"],
        }
        gaps: list[str] = []
        for type_name, props in required.items():
            if type_name not in types:
                continue
            for obj in _find_objects(blocks, {type_name}):
                for prop in props:
                    if prop not in obj:
                        gaps.append(f"{type_name}.{prop}")
        return sorted(set(gaps))

    def _rich_results(self, types: set[str]) -> CategoryResult:
        eligible = sorted(_RICH_TYPES & types)
        deprecated = sorted(_DEPRECATED_RICH & types)
        checks: list[CheckResult] = []

        if eligible:
            checks.append(
                CheckResult(
                    "rich_eligible_types", 100.0, FindingStatus.passed, Severity.low, _INF,
                    value="rich-result-eligible types: " + ", ".join(eligible),
                )
            )
        else:
            checks.append(
                CheckResult(
                    "rich_eligible_types", 50.0, FindingStatus.warn, Severity.low, _INF,
                    value="no rich-result-eligible structured data found",
                    recommendation="Add markup that can earn rich results where relevant.",
                )
            )

        if deprecated:
            checks.append(
                CheckResult(
                    "deprecated_rich", None, FindingStatus.info, Severity.info, _OBS,
                    value=(
                        "valid markup that no longer earns a rich result for most sites: "
                        + ", ".join(deprecated)
                    ),
                )
            )
        return CategoryResult("rich_results", scoring.category_score(checks), True, checks)

    def _entity(self, blocks: list) -> CategoryResult:
        orgs = _find_objects(blocks, _ORG_TYPES)
        if not orgs:
            check = CheckResult(
                "organization_entity", 40.0, FindingStatus.warn, Severity.medium, _OBS,
                value="no Organization entity markup",
                recommendation="Add Organization markup with name, logo and sameAs links.",
            )
            return CategoryResult("entity", scoring.category_score([check]), True, [check])

        org = orgs[0]
        has_sameas = bool(org.get("sameAs"))
        has_logo = bool(org.get("logo"))
        strong = has_sameas and has_logo
        details = []
        if has_sameas:
            details.append("sameAs")
        if has_logo:
            details.append("logo")
        check = CheckResult(
            "organization_entity",
            100.0 if strong else 70.0,
            FindingStatus.passed if strong else FindingStatus.warn,
            Severity.low,
            _OBS,
            value=(
                "Organization entity with " + ", ".join(details)
                if details
                else "Organization markup present but no sameAs or logo"
            ),
            recommendation=(
                None if strong else "Add sameAs links and a logo to strengthen the entity."
            ),
            evidence={"name": org.get("name")},
        )
        return CategoryResult("entity", scoring.category_score([check]), True, [check])
