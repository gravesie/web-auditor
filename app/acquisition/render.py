"""Headless render of a single page via Playwright.

Captures what the render-dependent audits need: the JavaScript-rendered DOM, the
cookies set, the third-party requests fired, and console errors. The page is
loaded with no interaction, so the cookies and requests seen here are those that
fire before any consent, which is the signal the compliance audit relies on.

Third-party is judged by registrable domain. The registrable-domain function uses
a small known-suffix list rather than the full Public Suffix List; good enough for
a first cut, to be replaced with the PSL when accuracy demands it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from app.acquisition.domains import registrable_domain

USER_AGENT = "WebAuditor/0.1 (+https://github.com/gravesie/web-auditor)"
NAV_TIMEOUT_MS = 30_000
SETTLE_MS = 1_500  # let late-firing trackers run after load


@dataclass
class RequestRecord:
    url: str
    resource_type: str
    method: str
    third_party: bool


@dataclass
class CookieRecord:
    name: str
    domain: str
    secure: bool
    http_only: bool
    same_site: str | None


@dataclass
class RenderResult:
    requested_url: str
    final_url: str | None = None
    status: int | None = None
    ok: bool = False
    title: str | None = None
    html: str = ""
    cookies: list[CookieRecord] = field(default_factory=list)
    requests: list[RequestRecord] = field(default_factory=list)
    console_errors: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def third_party_requests(self) -> list[RequestRecord]:
        return [r for r in self.requests if r.third_party]


def render(url: str, wait_until: str = "load", timeout_ms: int = NAV_TIMEOUT_MS) -> RenderResult:
    """Render a page and capture DOM, cookies, requests and console errors. Never raises."""
    target = url if "://" in url else f"https://{url}"
    result = RenderResult(requested_url=target)
    page_domain = registrable_domain(urlsplit(target).hostname or "")
    requests: list[RequestRecord] = []
    console_errors: list[str] = []

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()

            def on_request(req) -> None:
                host = urlsplit(req.url).hostname or ""
                third = registrable_domain(host) != page_domain
                requests.append(RequestRecord(req.url, req.resource_type, req.method, third))

            def on_console(msg) -> None:
                if msg.type == "error":
                    console_errors.append(msg.text[:500])

            page.on("request", on_request)
            page.on("console", on_console)

            response = page.goto(target, wait_until=wait_until, timeout=timeout_ms)
            page.wait_for_timeout(SETTLE_MS)

            result.final_url = page.url
            result.status = response.status if response else None
            result.ok = bool(response and response.ok)
            result.title = page.title()
            result.html = page.content()[:1_000_000]
            for c in context.cookies():
                result.cookies.append(
                    CookieRecord(
                        name=c.get("name", ""),
                        domain=c.get("domain", ""),
                        secure=bool(c.get("secure")),
                        http_only=bool(c.get("httpOnly")),
                        same_site=c.get("sameSite"),
                    )
                )
            browser.close()
    except PlaywrightError as exc:
        result.error = f"{type(exc).__name__}: {exc}"

    result.requests = requests
    result.console_errors = console_errors
    return result
