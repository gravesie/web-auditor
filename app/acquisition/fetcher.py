"""Outside-in acquisition for a single site.

Scoped to what the build-and-security audit needs for now: the homepage
response and headers, TLS certificate state, HTTP-to-HTTPS enforcement,
robots.txt, and a passive check of a couple of commonly-exposed paths. The
full crawl and Playwright render are later stages; this keeps the first audit
running end to end.

All network access is passive: ordinary GETs of public URLs, no exploitation.
"""

from __future__ import annotations

import re
import socket
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime

import httpx

USER_AGENT = "WebAuditor/0.1 (+https://github.com/gravesie/web-auditor)"
REQUEST_TIMEOUT = 15.0
TLS_TIMEOUT = 10.0

# Headers we expect a well-configured site to set.
SECURITY_HEADERS = [
    "strict-transport-security",
    "content-security-policy",
    "x-frame-options",
    "x-content-type-options",
    "referrer-policy",
    "permissions-policy",
]

# Paths that should never be publicly readable. Passive GET, status only.
SENSITIVE_PATHS = ["/.git/config", "/.env"]


@dataclass
class Acquisition:
    requested_url: str
    final_url: str | None = None
    status_code: int | None = None
    ok: bool = False
    headers: dict[str, str] = field(default_factory=dict)
    set_cookie: list[str] = field(default_factory=list)
    html: str = ""
    https_enforced: bool | None = None
    tls_valid: bool | None = None
    tls_not_after: datetime | None = None
    tls_days_left: int | None = None
    robots_txt: str | None = None
    sitemap_url: str | None = None
    sitemap_locs: list[str] = field(default_factory=list)
    exposed_paths: dict[str, int] = field(default_factory=dict)
    error: str | None = None


def _host(domain: str) -> str:
    """Reduce arbitrary user input to a bare host."""
    d = domain.strip().lower()
    d = d.split("://", 1)[-1]
    d = d.split("/", 1)[0]
    return d


def _tls_info(host: str) -> tuple[bool | None, datetime | None, int | None]:
    """Open a verified TLS connection and read the certificate expiry."""
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=TLS_TIMEOUT) as sock:
            with context.wrap_socket(sock, server_hostname=host) as ssock:
                cert = ssock.getpeercert()
    except (OSError, ssl.SSLError):
        # Handshake/verification failure or no HTTPS at all.
        return False, None, None

    not_after_raw = cert.get("notAfter") if cert else None
    if not not_after_raw:
        return True, None, None
    try:
        not_after = datetime.strptime(not_after_raw, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except ValueError:
        return True, None, None
    days_left = (not_after - datetime.now(UTC)).days
    return True, not_after, days_left


def _https_enforced(host: str) -> bool | None:
    """Does plain HTTP redirect through to HTTPS?"""
    try:
        resp = httpx.get(
            f"http://{host}/",
            follow_redirects=True,
            timeout=TLS_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )
        return resp.url.scheme == "https"
    except httpx.HTTPError:
        return None


_SITEMAP_IN_ROBOTS = re.compile(r"(?im)^\s*sitemap:\s*(\S+)")
_LOC_RE = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def _fetch_sitemap(
    client: httpx.Client, host: str, robots_txt: str | None
) -> tuple[str, list[str]]:
    """Locate the sitemap (from robots.txt or the default path) and parse its <loc> URLs."""
    url = None
    if robots_txt:
        match = _SITEMAP_IN_ROBOTS.search(robots_txt)
        if match:
            url = match.group(1).strip()
    if not url:
        url = f"https://{host}/sitemap.xml"
    try:
        resp = client.get(url)
    except httpx.HTTPError:
        return url, []
    if resp.status_code == 200 and ("<urlset" in resp.text or "<sitemapindex" in resp.text):
        return url, _LOC_RE.findall(resp.text)[:2000]
    return url, []


def fetch(domain: str) -> Acquisition:
    """Gather the outside-in dataset for one site. Never raises."""
    host = _host(domain)
    acq = Acquisition(requested_url=f"https://{host}/")
    headers = {"User-Agent": USER_AGENT}

    try:
        with httpx.Client(
            follow_redirects=True, timeout=REQUEST_TIMEOUT, headers=headers
        ) as client:
            resp = client.get(f"https://{host}/")
            acq.final_url = str(resp.url)
            acq.status_code = resp.status_code
            acq.ok = resp.is_success
            acq.headers = {k.lower(): v for k, v in resp.headers.items()}
            acq.set_cookie = resp.headers.get_list("set-cookie")
            acq.html = resp.text[:500_000]

            try:
                robots = client.get(f"https://{host}/robots.txt")
                if robots.status_code == 200 and "text" in robots.headers.get("content-type", ""):
                    acq.robots_txt = robots.text[:50_000]
            except httpx.HTTPError:
                pass

            acq.sitemap_url, acq.sitemap_locs = _fetch_sitemap(client, host, acq.robots_txt)

            for path in SENSITIVE_PATHS:
                try:
                    pr = client.get(f"https://{host}{path}")
                    acq.exposed_paths[path] = pr.status_code
                except httpx.HTTPError:
                    acq.exposed_paths[path] = -1
    except httpx.HTTPError as exc:
        acq.error = f"{type(exc).__name__}: {exc}"

    acq.https_enforced = _https_enforced(host)
    acq.tls_valid, acq.tls_not_after, acq.tls_days_left = _tls_info(host)
    return acq
