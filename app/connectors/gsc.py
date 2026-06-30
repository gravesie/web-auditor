"""Google Search Console client (Search Analytics + Sitemaps).

Thin httpx wrapper over the Search Console v3 REST API, plus pure helpers that
summarise the responses into what the audits consume. Keeping the parsing pure and
separate from the HTTP makes it testable on canned payloads without a live grant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

import httpx

API_BASE = "https://searchconsole.googleapis.com/webmasters/v3"

# GSC data lags ~2 days; ask for a 28-day window ending two days back.
WINDOW_DAYS = 28
DATA_LAG_DAYS = 2

# "Striking distance": ranking on page 1-2 but not the top few — the quick wins.
STRIKING_MIN_POSITION = 3.0
STRIKING_MAX_POSITION = 20.0

_TIMEOUT = httpx.Timeout(30.0)


class GscError(Exception):
    """A Search Console API request failed."""


@dataclass
class GscSummary:
    """What the audits read from Search Console for a site."""

    total_clicks: int
    total_impressions: int
    avg_position: float | None  # impression-weighted mean position
    top_queries: list[dict] = field(default_factory=list)
    striking_distance: list[dict] = field(default_factory=list)
    # Distinct pages that appeared in search over the window. A reliable lower bound
    # on indexed pages (the Sitemaps API "indexed" field is deprecated and returns 0).
    # 0 means no pages drew impressions, which does not by itself prove non-indexation.
    serving_pages: int = 0


def default_window(today: datetime | None = None) -> tuple[str, str]:
    """Return (start_date, end_date) ISO strings for the default reporting window."""
    end = (today or datetime.now(UTC)).date() - timedelta(days=DATA_LAG_DAYS)
    start = end - timedelta(days=WINDOW_DAYS - 1)
    return start.isoformat(), end.isoformat()


def _headers(access_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}"}


def _check(response: httpx.Response, what: str) -> None:
    if response.status_code != 200:
        raise GscError(f"{what} returned {response.status_code}: {response.text[:200]}")


def list_sites(access_token: str) -> list[dict]:
    """List the verified properties the grant can read (for the connect picker)."""
    try:
        response = httpx.get(f"{API_BASE}/sites", headers=_headers(access_token), timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise GscError(f"sites.list failed: {exc}") from exc
    _check(response, "sites.list")
    entries = response.json().get("siteEntry", [])
    # Only properties the user can actually pull data for.
    return [e for e in entries if e.get("permissionLevel") != "siteUnverifiedUser"]


class GscClient:
    """Per-property Search Console client."""

    def __init__(self, access_token: str, site_url: str):
        self.access_token = access_token
        self.site_url = site_url
        self._encoded = quote(site_url, safe="")

    def search_analytics(
        self, start_date: str, end_date: str, *, dimensions: list[str], row_limit: int = 1000
    ) -> list[dict]:
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimensions,
            "rowLimit": row_limit,
        }
        url = f"{API_BASE}/sites/{self._encoded}/searchAnalytics/query"
        try:
            response = httpx.post(
                url, headers=_headers(self.access_token), json=body, timeout=_TIMEOUT
            )
        except httpx.HTTPError as exc:
            raise GscError(f"searchAnalytics.query failed: {exc}") from exc
        _check(response, "searchAnalytics.query")
        return response.json().get("rows", [])

    def sitemaps(self) -> list[dict]:
        url = f"{API_BASE}/sites/{self._encoded}/sitemaps"
        try:
            response = httpx.get(url, headers=_headers(self.access_token), timeout=_TIMEOUT)
        except httpx.HTTPError as exc:
            raise GscError(f"sitemaps.list failed: {exc}") from exc
        _check(response, "sitemaps.list")
        return response.json().get("sitemap", [])

    def fetch_summary(self, window: tuple[str, str] | None = None) -> GscSummary:
        """Pull query- and page-level Search Analytics and summarise it for the audits."""
        start, end = window or default_window()
        query_rows = self.search_analytics(start, end, dimensions=["query"])
        summary = summarise_queries(query_rows)
        page_rows = self.search_analytics(start, end, dimensions=["page"])
        summary.serving_pages = count_serving_pages(page_rows)
        return summary


def summarise_queries(rows: list[dict]) -> GscSummary:
    """Reduce raw query rows into totals, weighted position, and the notable lists."""
    total_clicks = 0
    total_impressions = 0
    weighted_position = 0.0
    for row in rows:
        impressions = int(row.get("impressions", 0) or 0)
        total_clicks += int(row.get("clicks", 0) or 0)
        total_impressions += impressions
        weighted_position += float(row.get("position", 0) or 0) * impressions

    avg_position = (weighted_position / total_impressions) if total_impressions else None

    top_queries = sorted(rows, key=lambda r: r.get("clicks", 0), reverse=True)[:10]
    striking = [
        r
        for r in rows
        if STRIKING_MIN_POSITION <= float(r.get("position", 0) or 0) <= STRIKING_MAX_POSITION
    ]
    striking.sort(key=lambda r: r.get("impressions", 0), reverse=True)

    return GscSummary(
        total_clicks=total_clicks,
        total_impressions=total_impressions,
        avg_position=avg_position,
        top_queries=[_query_row(r) for r in top_queries],
        striking_distance=[_query_row(r) for r in striking[:10]],
    )


def count_serving_pages(rows: list[dict]) -> int:
    """Count distinct pages that drew at least one impression in the window.

    Uses the `page` dimension of Search Analytics, which reflects pages Google
    actually served — a trustworthy indexation signal, unlike the deprecated
    Sitemaps API `indexed` field.
    """
    return sum(1 for row in rows if int(row.get("impressions", 0) or 0) > 0)


def _query_row(row: dict) -> dict:
    """Flatten a single-dimension query row to a tidy record."""
    keys = row.get("keys") or []
    return {
        "query": keys[0] if keys else "",
        "clicks": int(row.get("clicks", 0) or 0),
        "impressions": int(row.get("impressions", 0) or 0),
        "ctr": round(float(row.get("ctr", 0) or 0), 4),
        "position": round(float(row.get("position", 0) or 0), 1),
    }
