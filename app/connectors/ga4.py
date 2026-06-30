"""Google Analytics 4 client (Admin API for discovery, Data API for reporting).

The Admin API lists the properties a grant can read (used by the connect picker);
the Data API pulls a small aggregate report (engagement + key events) for the audits.
As with the GSC client, response parsing is a pure helper so it can be tested on
canned payloads.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

ADMIN_BASE = "https://analyticsadmin.googleapis.com/v1beta"
DATA_BASE = "https://analyticsdata.googleapis.com/v1beta"

# Aggregate metrics over the last 28 complete days. "conversions" was renamed to
# "keyEvents" in GA4; we request keyEvents.
REPORT_METRICS = [
    "sessions",
    "totalUsers",
    "engagementRate",
    "averageSessionDuration",
    "keyEvents",
]
REPORT_START = "28daysAgo"
REPORT_END = "yesterday"

_TIMEOUT = httpx.Timeout(30.0)


class Ga4Error(Exception):
    """A Google Analytics API request failed."""


@dataclass
class Ga4Summary:
    """What the audits read from GA4 for a site."""

    sessions: int
    total_users: int
    engagement_rate: float | None  # 0..1
    avg_session_duration: float | None  # seconds
    key_events: int  # conversions / key events count


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _check(response: httpx.Response, what: str) -> None:
    if response.status_code != 200:
        raise Ga4Error(f"{what} returned {response.status_code}: {response.text[:200]}")


def _property_id(raw: str) -> str:
    """Normalise 'properties/123' or '123' to the bare numeric id the Data API wants."""
    return raw.split("/")[-1].strip()


def list_properties(access_token: str) -> list[dict]:
    """List GA4 properties the grant can read, as {id, name, account} (connect picker)."""
    try:
        response = httpx.get(
            f"{ADMIN_BASE}/accountSummaries", headers=_headers(access_token), timeout=_TIMEOUT
        )
    except httpx.HTTPError as exc:
        raise Ga4Error(f"accountSummaries.list failed: {exc}") from exc
    _check(response, "accountSummaries.list")

    properties: list[dict] = []
    for account in response.json().get("accountSummaries", []):
        account_name = account.get("displayName", "")
        for prop in account.get("propertySummaries", []):
            properties.append(
                {
                    "id": _property_id(prop.get("property", "")),
                    "name": prop.get("displayName", ""),
                    "account": account_name,
                }
            )
    return properties


class Ga4Client:
    """Per-property GA4 Data API client."""

    def __init__(self, access_token: str, property_id: str):
        self.access_token = access_token
        self.property_id = _property_id(property_id)

    def run_report(self, metrics: list[str], start: str, end: str) -> dict:
        body = {
            "dateRanges": [{"startDate": start, "endDate": end}],
            "metrics": [{"name": m} for m in metrics],
        }
        url = f"{DATA_BASE}/properties/{self.property_id}:runReport"
        try:
            response = httpx.post(
                url, headers=_headers(self.access_token), json=body, timeout=_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise Ga4Error(f"runReport failed: {exc}") from exc
        _check(response, "runReport")
        return response.json()

    def fetch_summary(self) -> Ga4Summary:
        report = self.run_report(REPORT_METRICS, REPORT_START, REPORT_END)
        return summarise_report(report)


def summarise_report(report: dict) -> Ga4Summary:
    """Map a runReport response into a Ga4Summary by metric name (order-independent)."""
    headers = [h.get("name", "") for h in report.get("metricHeaders", [])]
    rows = report.get("rows", [])
    if not rows:
        # No traffic in the window (or an empty property): report zeros, not an error.
        return Ga4Summary(0, 0, None, None, 0)

    values = rows[0].get("metricValues", [])
    by_name = {name: values[i].get("value") for i, name in enumerate(headers) if i < len(values)}

    return Ga4Summary(
        sessions=_as_int(by_name.get("sessions")),
        total_users=_as_int(by_name.get("totalUsers")),
        engagement_rate=_as_float(by_name.get("engagementRate")),
        avg_session_duration=_as_float(by_name.get("averageSessionDuration")),
        key_events=_as_int(by_name.get("keyEvents")),
    )


def _as_int(raw: object) -> int:
    try:
        return int(float(raw)) if raw is not None else 0
    except (TypeError, ValueError):
        return 0


def _as_float(raw: object) -> float | None:
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
