"""Bounded internal crawler.

Breadth-first from the homepage over httpx, capped by page count and depth, to
enumerate the site's pages (the site -> page level) and record click depth for the
SEO audits. JavaScript-only links are missed here; the Playwright render covers the
pages where that matters.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass
from urllib.parse import urldefrag, urljoin, urlsplit

import httpx

from app.acquisition.domains import registrable_domain

USER_AGENT = "WebAuditor/0.1 (+https://github.com/gravesie/web-auditor)"
MAX_PAGES = 50
MAX_DEPTH = 3
REQUEST_TIMEOUT = 15.0

_LINK_RE = re.compile(r'<a\s[^>]*?href=["\']([^"\']+)["\']', re.I)


@dataclass
class CrawledPage:
    url: str
    depth: int
    status: int | None


def _host(domain: str) -> str:
    target = domain if "://" in domain else f"https://{domain}"
    return (urlsplit(target).hostname or "").lower()


def crawl(domain: str, max_pages: int = MAX_PAGES, max_depth: int = MAX_DEPTH) -> list[CrawledPage]:
    host = _host(domain)
    if not host:
        return []
    site_domain = registrable_domain(host)
    start = f"https://{host}/"
    seen: set[str] = {start}
    done: set[str] = set()  # final URLs already recorded, to dedupe across redirects
    pages: list[CrawledPage] = []
    queue: deque[tuple[str, int]] = deque([(start, 0)])

    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(follow_redirects=True, timeout=REQUEST_TIMEOUT, headers=headers) as client:
        while queue and len(pages) < max_pages:
            url, depth = queue.popleft()
            try:
                resp = client.get(url)
            except httpx.HTTPError:
                pages.append(CrawledPage(url, depth, None))
                continue

            final = str(resp.url)
            if final in done:
                continue
            done.add(final)
            pages.append(CrawledPage(final, depth, resp.status_code))

            if depth >= max_depth or "html" not in resp.headers.get("content-type", ""):
                continue

            for href in _LINK_RE.findall(resp.text):
                absolute = urldefrag(urljoin(str(resp.url), href))[0]
                parsed = urlsplit(absolute)
                if parsed.scheme not in ("http", "https"):
                    continue
                if registrable_domain(parsed.hostname or "") != site_domain:
                    continue
                if absolute not in seen and len(seen) < max_pages * 4:
                    seen.add(absolute)
                    queue.append((absolute, depth + 1))

    return pages
