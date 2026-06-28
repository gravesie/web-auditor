"""Compliance audit: trackers-before-consent and transport, offline (no policy fetch)."""

from app.acquisition.fetcher import Acquisition
from app.acquisition.render import RenderResult, RequestRecord
from app.audits.base import AuditContext
from app.audits.compliance import ComplianceAudit
from app.models.enums import FindingStatus


def _run(acq, render):
    ctx = AuditContext(site_domain="x", data={"acquisition": acq, "render": render})
    return ComplianceAudit().run(ctx)


def _findings(result):
    return {f.key: f for c in result.categories for f in c.checks}


def _acq():
    # Empty HTML means no privacy link, so the audit never makes a network fetch.
    return Acquisition(
        requested_url="https://x/",
        final_url="https://x/",
        https_enforced=True,
        html="<html></html>",
    )


def test_tracker_before_consent_is_a_breach():
    render = RenderResult(
        requested_url="https://x/", final_url="https://x/", ok=True, html="<html></html>",
        requests=[
            RequestRecord("https://www.google-analytics.com/g/collect", "script", "GET", True)
        ],
        cookies=[],
    )
    findings = _findings(_run(_acq(), render))
    breach = findings["trackers_before_consent"]
    assert breach.status == FindingStatus.fail
    assert "google-analytics.com" in breach.evidence["trackers"]


def test_clean_render_has_no_tracker_breach():
    render = RenderResult(
        requested_url="https://x/", final_url="https://x/", ok=True, html="<html></html>",
        requests=[], cookies=[],
    )
    findings = _findings(_run(_acq(), render))
    assert findings["trackers_before_consent"].status == FindingStatus.passed


def test_us_processor_transfer_flagged_with_dpf_wording():
    render = RenderResult(
        requested_url="https://x/", final_url="https://x/", ok=True, html="<html></html>",
        requests=[RequestRecord("https://connect.facebook.net/pixel.js", "script", "GET", True)],
        cookies=[],
    )
    findings = _findings(_run(_acq(), render))
    transfer = findings["us_processor_transfer"]
    assert transfer.status == FindingStatus.warn
    assert "facebook.net" in transfer.evidence["us_processors"]
