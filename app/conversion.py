"""Shared detection of on-page conversion routes and calls to action.

The messaging audit (13.9) inventories the conversion systems a site offers, and
the content-strategy audit (13.10) asks whether content leads anywhere. Both look
for the same signals, so the detection lives here once.

Detection is from the rendered DOM and visible text, signature-based and
conservative: a route is reported only when its marker is present.
"""

from __future__ import annotations

import re

_FORM_RE = re.compile(r"<form\b", re.I)
_CHAT_RE = re.compile(r"intercom|drift\.com|tawk\.to|livechat|crisp\.chat|zendesk|hubspot", re.I)
_BOOKING_RE = re.compile(r"calendly|cal\.com|acuity|savvycal|book(?:ing)?", re.I)
_CTA_RE = re.compile(
    r"get started|contact us|book a|book now|sign up|request a|get a quote|"
    r"free trial|get in touch|talk to|request demo|book a demo|buy now|shop now|"
    r"subscribe|start (?:free|now)|get your",
    re.I,
)


def detect_conversion_systems(dom: str) -> list[str]:
    """The visible conversion routes in a rendered DOM: form, email, phone, booking, chat."""
    systems: list[str] = []
    if _FORM_RE.search(dom):
        systems.append("form")
    if "mailto:" in dom.lower():
        systems.append("email")
    if "tel:" in dom.lower():
        systems.append("phone")
    if _BOOKING_RE.search(dom):
        systems.append("booking")
    if _CHAT_RE.search(dom):
        systems.append("live chat")
    return systems


def has_cta(text: str) -> bool:
    """True when the visible text carries a recognisable call to action."""
    return bool(_CTA_RE.search(text))
