"""Capture a screenshot of a URL for the onboarding confirm step.

Best-effort and self-contained: launches headless Chromium, loads the page, and
returns PNG bytes, or None if anything goes wrong (bad host, timeout). Never raises,
so the funnel keeps working even when a site won't render.
"""

from __future__ import annotations

import logging

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

logger = logging.getLogger(__name__)


def capture_screenshot(url: str, timeout_ms: int = 15000) -> bytes | None:
    """A viewport PNG of the page, or None on any failure."""
    target = url if "://" in url else f"https://{url}"
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                page.goto(target, wait_until="load", timeout=timeout_ms)
                return page.screenshot(type="png")
            finally:
                browser.close()
    except (PlaywrightError, OSError) as exc:
        logger.warning("screenshot failed for %s: %s", target, exc)
        return None
