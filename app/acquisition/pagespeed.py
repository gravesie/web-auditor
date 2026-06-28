"""PageSpeed Insights (v5) client.

Returns both Lighthouse lab metrics and Chrome UX Report (CrUX) field data for a
URL. Field data only exists for URLs/origins with enough real-user traffic, so
small sites come back lab-only. The call runs Lighthouse server-side and can take
20-40 seconds. Never raises; failures come back as ok=False.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

ENDPOINT = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"
TIMEOUT = 70.0

_LAB_AUDITS = {
    "fcp": "first-contentful-paint",
    "lcp": "largest-contentful-paint",
    "tbt": "total-blocking-time",
    "cls": "cumulative-layout-shift",
    "speed_index": "speed-index",
    "ttfb": "server-response-time",
}
_FIELD_METRICS = {
    "lcp": "LARGEST_CONTENTFUL_PAINT_MS",
    "inp": "INTERACTION_TO_NEXT_PAINT",
    "cls": "CUMULATIVE_LAYOUT_SHIFT_SCORE",
    "fcp": "FIRST_CONTENTFUL_PAINT_MS",
}


@dataclass
class FieldMetric:
    value: float  # ms, except CLS which is unitless
    category: str  # FAST / AVERAGE / SLOW


@dataclass
class PageSpeedResult:
    ok: bool
    strategy: str
    error: str | None = None
    lighthouse_score: float | None = None  # 0..100
    lab: dict[str, float] = field(default_factory=dict)
    field: dict[str, FieldMetric] = field(default_factory=dict)
    field_overall: str | None = None
    total_bytes: int | None = None

    @property
    def has_field(self) -> bool:
        return bool(self.field)


def fetch_pagespeed(
    url: str, api_key: str | None = None, strategy: str = "mobile"
) -> PageSpeedResult:
    params = {"url": url, "strategy": strategy, "category": "performance"}
    if api_key:
        params["key"] = api_key
    try:
        resp = httpx.get(ENDPOINT, params=params, timeout=TIMEOUT)
    except httpx.HTTPError as exc:
        return PageSpeedResult(ok=False, strategy=strategy, error=f"{type(exc).__name__}: {exc}")
    if resp.status_code != 200:
        return PageSpeedResult(ok=False, strategy=strategy, error=f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError:
        return PageSpeedResult(ok=False, strategy=strategy, error="invalid JSON")
    return _parse(data, strategy)


def _parse(data: dict, strategy: str) -> PageSpeedResult:
    result = PageSpeedResult(ok=True, strategy=strategy)

    lighthouse = data.get("lighthouseResult", {})
    perf = lighthouse.get("categories", {}).get("performance", {})
    if perf.get("score") is not None:
        result.lighthouse_score = perf["score"] * 100

    audits = lighthouse.get("audits", {})
    for label, key in _LAB_AUDITS.items():
        value = audits.get(key, {}).get("numericValue")
        if value is not None:
            result.lab[label] = value
    total = audits.get("total-byte-weight", {}).get("numericValue")
    if total is not None:
        result.total_bytes = int(total)

    experience = data.get("loadingExperience") or {}
    metrics = experience.get("metrics") or {}
    if not metrics:
        experience = data.get("originLoadingExperience") or {}
        metrics = experience.get("metrics") or {}
    for label, key in _FIELD_METRICS.items():
        metric = metrics.get(key)
        if metric and "percentile" in metric:
            value = metric["percentile"]
            if label == "cls":  # CrUX reports CLS x100
                value = value / 100.0
            result.field[label] = FieldMetric(value=value, category=metric.get("category", ""))
    result.field_overall = experience.get("overall_category")

    return result
