"""Build quality and security audit (spec 13.2).

Implemented: security posture (the 35-weight core, fully observable); version
currency with end-of-life detection; the AI-build heuristic (rule-based builder
fingerprints and boilerplate-copy signals, a light scoring lever); the observable
part of maintenance signals; and the technology fingerprint as an informational
finding. Still a later enhancement: full per-CVE lookup against NVD, and the
crawl-based maintenance signals (broken links, console errors), which report as
not-yet-assessed rather than guessing a score.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from app import scoring
from app.acquisition.fetcher import SECURITY_HEADERS, Acquisition
from app.audits.base import (
    AuditContext,
    AuditModule,
    AuditResult,
    CategoryDef,
    CategoryResult,
    CheckResult,
)
from app.models.enums import DetectionTag, FindingStatus, Severity

CATEGORIES = [
    CategoryDef("security_posture", "Security posture", 35),
    CategoryDef("versions_vulns", "Version currency and known vulnerabilities", 25),
    CategoryDef("maintenance", "Maintenance signals", 25),
    CategoryDef("ai_tells", "Build-effort and AI tells", 10),
    CategoryDef("fingerprint", "Technology fingerprint", 5),
]
_DEFS = {c.key: c for c in CATEGORIES}

_OBS = DetectionTag.observed
_INF = DetectionTag.inferred
_NEEDS = DetectionTag.needs_connection

# PHP below 8.1 is end-of-life (8.0 reached EOL in November 2023; all 7.x earlier).
# Kept deliberately small: we only claim end-of-life where it is unambiguous.
_PHP_EOL_BELOW = (8, 1)

# Site/AI builder signatures found in markup or headers. "ai" means a builder that
# generates whole sites (a strong signal); "template" is a drag-drop builder (mild).
_BUILDER_SIGNATURES: dict[str, tuple[str, str]] = {
    "durable": ("Durable", "ai"),
    "framer": ("Framer", "ai"),
    "10web": ("10Web", "ai"),
    "wix.com": ("Wix", "template"),
    "squarespace": ("Squarespace", "template"),
    "weebly": ("Weebly", "template"),
    "godaddy website builder": ("GoDaddy Website Builder", "template"),
    "jimdo": ("Jimdo", "template"),
    "carrd": ("Carrd", "template"),
}

# Generic / placeholder copy patterns that often survive into low-effort builds.
_BOILERPLATE_PATTERNS = [
    r"lorem ipsum",
    r"your (?:trusted )?partner",
    r"welcome to (?:our |my )?(?:website|site)",
    r"your one[- ]stop",
    r"leading provider of",
    r"we are a team of",
    r"your text here",
    r"\[(?:business|company|your) name\]",
]


def _php_eol(version: str) -> bool:
    try:
        major, minor = (int(p) for p in version.split(".")[:2])
    except ValueError:
        return False
    return (major, minor) < _PHP_EOL_BELOW


def _extract_components(acq: Acquisition) -> list[tuple[str, str]]:
    """Pull (product, version) pairs from headers and the generator meta tag."""
    found: list[tuple[str, str]] = []
    for header in ("server", "x-powered-by"):
        value = acq.headers.get(header, "")
        match = re.search(r"([a-zA-Z][\w.\-]*?)/(\d+(?:\.\d+)+)", value)
        if match:
            found.append((match.group(1).lower(), match.group(2)))
    generator = re.search(
        r'<meta\s+name=["\']generator["\']\s+content=["\']([^"\']+)["\']', acq.html, re.I
    )
    if generator:
        cms = re.search(r"(wordpress|drupal|joomla)\s+(\d+(?:\.\d+)+)", generator.group(1), re.I)
        if cms:
            found.append((cms.group(1).lower(), cms.group(2)))
    return found


def _passfail(value: bool | None, score_true: float = 100.0) -> tuple[float, FindingStatus]:
    if value is None:
        return 0.0, FindingStatus.warn
    if value:
        return score_true, FindingStatus.passed
    return 0.0, FindingStatus.fail


def _tri(value: bool | None, yes: str, no: str) -> str:
    """Render a tri-state boolean for a finding value."""
    if value is None:
        return "undetermined"
    return yes if value else no


class BuildSecurityAudit(AuditModule):
    key = "build_security"
    label = "Build quality and security"
    categories = CATEGORIES

    def run(self, context: AuditContext) -> AuditResult:
        acq: Acquisition = context.data["acquisition"]
        cats = [
            self._security_posture(acq),
            self._maintenance(acq),
            self._fingerprint(acq),
            self._versions_vulns(acq),
            self._ai_tells(acq),
        ]
        return AuditResult(
            audit_key=self.key,
            score=scoring.audit_score(cats, _DEFS),
            completeness=scoring.completeness(cats),
            categories=cats,
        )

    # --- security posture -------------------------------------------------

    def _security_posture(self, acq: Acquisition) -> CategoryResult:
        checks: list[CheckResult] = []

        enforced = acq.https_enforced
        score, status = _passfail(enforced)
        checks.append(
            CheckResult(
                "https_enforced", score, status, Severity.high, _OBS,
                value=_tri(enforced, "yes", "no"),
                recommendation=None if enforced else "Redirect all HTTP traffic to HTTPS.",
            )
        )

        valid = acq.tls_valid
        score, status = _passfail(valid)
        checks.append(
            CheckResult(
                "tls_valid", score, status, Severity.high, _OBS,
                value=_tri(valid, "valid", "invalid or absent"),
                recommendation=None if valid else "Serve a valid, trusted TLS certificate.",
            )
        )

        checks.append(self._tls_expiry(acq))

        for header in SECURITY_HEADERS:
            present = header in acq.headers
            checks.append(
                CheckResult(
                    f"header_{header.replace('-', '_')}",
                    100.0 if present else 0.0,
                    FindingStatus.passed if present else FindingStatus.fail,
                    Severity.medium,
                    _OBS,
                    value="present" if present else "missing",
                    recommendation=None if present else f"Set the {header} response header.",
                )
            )

        checks.append(self._mixed_content(acq))
        checks.append(self._exposed_paths(acq))
        checks.append(self._cookie_flags(acq))
        checks.append(self._info_leakage(acq))

        return CategoryResult("security_posture", scoring.category_score(checks), True, checks)

    def _tls_expiry(self, acq: Acquisition) -> CheckResult:
        days = acq.tls_days_left
        if days is None:
            return CheckResult(
                "tls_expiry", 0.0, FindingStatus.warn, Severity.high, _OBS,
                value="expiry undetermined",
            )
        if days > 30:
            score, status = 100.0, FindingStatus.passed
        elif days > 7:
            score, status = 50.0, FindingStatus.warn
        else:
            score, status = 0.0, FindingStatus.fail
        return CheckResult(
            "tls_expiry", score, status, Severity.high, _OBS,
            value=f"{days} days until expiry",
            recommendation=None if days > 30 else "Renew the TLS certificate.",
        )

    def _mixed_content(self, acq: Acquisition) -> CheckResult:
        on_https = (acq.final_url or "").startswith("https")
        insecure = re.findall(r'(?:src|href)=["\']http://', acq.html) if acq.html else []
        if not on_https or not insecure:
            return CheckResult(
                "mixed_content", 100.0, FindingStatus.passed, Severity.medium, _OBS,
                value="none detected",
            )
        return CheckResult(
            "mixed_content", 0.0, FindingStatus.fail, Severity.medium, _OBS,
            value=f"{len(insecure)} insecure resource references",
            recommendation="Load all resources over HTTPS.",
        )

    def _exposed_paths(self, acq: Acquisition) -> CheckResult:
        exposed = [p for p, st in acq.exposed_paths.items() if st == 200]
        if not exposed:
            return CheckResult(
                "exposed_paths", 100.0, FindingStatus.passed, Severity.critical, _OBS,
                value="none exposed",
            )
        return CheckResult(
            "exposed_paths", 0.0, FindingStatus.fail, Severity.critical, _OBS,
            value=f"publicly readable: {', '.join(exposed)}",
            recommendation="Block public access to these paths immediately.",
            evidence={"exposed": exposed},
        )

    def _cookie_flags(self, acq: Acquisition) -> CheckResult:
        if not acq.set_cookie:
            return CheckResult(
                "cookie_flags", 100.0, FindingStatus.passed, Severity.low, _OBS,
                value="no cookies set on the homepage",
            )
        flags = ("secure", "httponly", "samesite")
        all_good = all(all(f in c.lower() for f in flags) for c in acq.set_cookie)
        if all_good:
            return CheckResult(
                "cookie_flags", 100.0, FindingStatus.passed, Severity.low, _OBS,
                value="all cookies set Secure, HttpOnly and SameSite",
            )
        return CheckResult(
            "cookie_flags", 50.0, FindingStatus.warn, Severity.medium, _OBS,
            value="one or more cookies missing Secure/HttpOnly/SameSite",
            recommendation="Set Secure, HttpOnly and SameSite on cookies.",
        )

    def _info_leakage(self, acq: Acquisition) -> CheckResult:
        server = acq.headers.get("server", "")
        powered_by = acq.headers.get("x-powered-by", "")
        leaks_version = bool(re.search(r"\d+\.\d+", server)) or bool(powered_by)
        if not leaks_version:
            return CheckResult(
                "info_leakage", 100.0, FindingStatus.passed, Severity.low, _OBS,
                value="no obvious version disclosure",
            )
        return CheckResult(
            "info_leakage", 50.0, FindingStatus.warn, Severity.low, _OBS,
            value=f"Server: {server!r}; X-Powered-By: {powered_by!r}",
            recommendation="Suppress version details in Server and X-Powered-By headers.",
        )

    # --- maintenance ------------------------------------------------------

    def _maintenance(self, acq: Acquisition) -> CategoryResult:
        checks = [self._copyright_year(acq)]
        # Crawl-based maintenance signals need the crawl/render stage.
        checks.append(
            CheckResult(
                "crawl_signals", None, FindingStatus.info, Severity.info, _NEEDS,
                value="broken links and console errors need the crawl/render stage (not yet built)",
            )
        )
        return CategoryResult("maintenance", scoring.category_score(checks), True, checks)

    def _copyright_year(self, acq: Acquisition) -> CheckResult:
        years = re.findall(r"(?:©|&copy;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", acq.html, re.I)
        if not years:
            return CheckResult(
                "copyright_year", None, FindingStatus.info, Severity.info, _INF,
                value="no copyright year found in the homepage",
            )
        latest = max(int(y) for y in years)
        current = datetime.now(UTC).year
        stale = latest < current - 1
        return CheckResult(
            "copyright_year",
            50.0 if stale else 100.0,
            FindingStatus.warn if stale else FindingStatus.passed,
            Severity.low,
            _INF,
            value=f"footer year {latest}",
            recommendation="Update the footer copyright year." if stale else None,
        )

    # --- fingerprint (informational, not scored) --------------------------

    def _fingerprint(self, acq: Acquisition) -> CategoryResult:
        server = acq.headers.get("server")
        powered_by = acq.headers.get("x-powered-by")
        generator_match = re.search(
            r'<meta\s+name=["\']generator["\']\s+content=["\']([^"\']+)["\']', acq.html, re.I
        )
        generator = generator_match.group(1) if generator_match else None
        parts = [p for p in (server, powered_by, generator) if p]
        value = "; ".join(parts) if parts else "no obvious stack signature"
        check = CheckResult(
            "stack_detected", None, FindingStatus.info, Severity.info, _OBS,
            value=value,
            evidence={"server": server, "x_powered_by": powered_by, "generator": generator},
        )
        return CategoryResult("fingerprint", None, False, [check])

    # --- version currency and known vulnerabilities -----------------------

    def _versions_vulns(self, acq: Acquisition) -> CategoryResult:
        components = _extract_components(acq)
        checks: list[CheckResult] = []

        if not components:
            # Nothing disclosed. We cannot assess currency from outside, and not
            # disclosing versions is itself good practice, so this is not a fault.
            checks.append(
                CheckResult(
                    "version_disclosure", None, FindingStatus.info, Severity.info, _OBS,
                    value="no component versions disclosed; currency not assessable from outside",
                )
            )
            return CategoryResult("versions_vulns", scoring.category_score(checks), True, checks)

        eol_components: list[str] = []
        for product, version in components:
            eol = product == "php" and _php_eol(version)
            if eol:
                eol_components.append(f"{product} {version}")
            checks.append(
                CheckResult(
                    f"component_{product}",
                    0.0 if eol else 100.0,
                    FindingStatus.fail if eol else FindingStatus.passed,
                    Severity.high if eol else Severity.low,
                    _OBS,
                    value=f"{product} {version}" + (" (end-of-life)" if eol else ""),
                    recommendation=(
                        f"Upgrade {product}; this version no longer receives security updates."
                        if eol
                        else None
                    ),
                )
            )

        if eol_components:
            checks.append(
                CheckResult(
                    "known_vulnerabilities", 0.0, FindingStatus.fail, Severity.high, _INF,
                    value=(
                        "end-of-life components carry unpatched known vulnerabilities: "
                        + ", ".join(eol_components)
                    ),
                    recommendation="Update to a supported version.",
                )
            )
        else:
            checks.append(
                CheckResult(
                    "known_vulnerabilities", 100.0, FindingStatus.passed, Severity.medium, _INF,
                    value=(
                        "no end-of-life components detected "
                        "(full per-CVE lookup is a later enhancement)"
                    ),
                )
            )

        return CategoryResult("versions_vulns", scoring.category_score(checks), True, checks)

    # --- build-effort and AI tells (heuristic, light lever) ---------------

    def _ai_tells(self, acq: Acquisition) -> CategoryResult:
        html_lower = acq.html.lower()
        server = acq.headers.get("server", "").lower()
        signals: list[str] = []
        confidence = 0
        builder_label: str | None = None

        for signature, (label, kind) in _BUILDER_SIGNATURES.items():
            if signature in html_lower or signature in server:
                builder_label = label
                if kind == "ai":
                    signals.append(f"built with {label} (AI website builder)")
                    confidence += 60
                else:
                    signals.append(f"built with {label} (template builder)")
                    confidence += 20
                break

        boilerplate_hits = [p for p in _BOILERPLATE_PATTERNS if re.search(p, html_lower)]
        if boilerplate_hits:
            signals.append(f"{len(boilerplate_hits)} generic or placeholder copy pattern(s)")
            confidence += min(30, 10 * len(boilerplate_hits))

        if "mailto:" not in html_lower and "tel:" not in html_lower:
            signals.append("no contact email or phone link on the homepage")
            confidence += 10

        confidence = min(100, confidence)
        high = confidence >= 50
        value = (
            f"{confidence}% likelihood of an AI or low-effort build; signals suggest: "
            + "; ".join(signals)
            if signals
            else "no AI-build signals detected"
        )
        check = CheckResult(
            "ai_build_likelihood",
            float(100 - confidence),
            FindingStatus.warn if high else FindingStatus.passed,
            Severity.low,
            _INF,
            value=value,
            recommendation=(
                "Review whether the site reflects the real business rather than a generic template."
                if high
                else None
            ),
            evidence={"confidence": confidence, "signals": signals, "builder": builder_label},
        )
        return CategoryResult("ai_tells", scoring.category_score([check]), True, [check])
