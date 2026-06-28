"""Build-and-security audit: security posture and version detection."""

from app.acquisition.fetcher import SECURITY_HEADERS, Acquisition
from app.audits.base import AuditContext
from app.audits.build_security import BuildSecurityAudit, _extract_components, _php_eol
from app.models.enums import FindingStatus


def _run(acq):
    return BuildSecurityAudit().run(AuditContext(site_domain="x", data={"acquisition": acq}))


def _findings(result):
    return {f.key: f for c in result.categories for f in c.checks}


def test_secure_site_scores_high():
    headers = dict.fromkeys(SECURITY_HEADERS, "x")
    headers["server"] = "cloudflare"
    acq = Acquisition(
        requested_url="https://x/", final_url="https://x/", status_code=200, ok=True,
        headers=headers, html="<html></html>", https_enforced=True, tls_valid=True,
        tls_days_left=90, exposed_paths={"/.git/config": 404, "/.env": 404},
    )
    result = _run(acq)
    posture = next(c for c in result.categories if c.key == "security_posture")
    assert posture.score >= 90
    assert result.score is not None


def test_insecure_site_is_flagged():
    acq = Acquisition(
        requested_url="https://x/", final_url="https://x/", status_code=200, ok=True,
        headers={}, html="<html></html>", https_enforced=False, tls_valid=False,
        tls_days_left=None, exposed_paths={"/.git/config": 200},
    )
    findings = _findings(_run(acq))
    assert findings["https_enforced"].status == FindingStatus.fail
    assert findings["exposed_paths"].status == FindingStatus.fail
    assert findings["header_content_security_policy"].status == FindingStatus.fail


def test_php_end_of_life_detection():
    assert _php_eol("7.4")
    assert _php_eol("8.0")
    assert not _php_eol("8.1")
    assert not _php_eol("8.3")


def test_component_extraction():
    acq = Acquisition(
        requested_url="x",
        headers={"server": "Apache/2.4.41", "x-powered-by": "PHP/7.4.3"},
        html='<meta name="generator" content="WordPress 5.4.1">',
    )
    components = dict(_extract_components(acq))
    assert components["php"] == "7.4.3"
    assert components["apache"] == "2.4.41"
    assert components["wordpress"] == "5.4.1"


def test_end_of_life_component_fails_known_vulnerabilities():
    acq = Acquisition(requested_url="x", headers={"x-powered-by": "PHP/7.4.3"}, html="")
    findings = _findings(_run(acq))
    assert findings["known_vulnerabilities"].status == FindingStatus.fail
