"""Public onboarding helpers: input validation and the visitor's audit allowance.

The landing funnel is unauthenticated, so validation is the front line: the URL box
accepts only a web address, the email box only an email. A visitor gets a fixed
number of audits (the free-plan site cap) before being asked to create an account or
talk to us, and their results only go out once we have their email. Those rules are
the abuse protection.
"""

from __future__ import annotations

import re

from app.plans import max_sites_for

# One anonymous visitor's audits run under a single free-plan account, so the cap is
# the free-plan site limit.
FREE_PLAN = "free"
MAX_VISITOR_AUDITS = max_sites_for(FREE_PLAN) or 5

# A hostname: labels of letters/digits/hyphens, at least one dot, a sane TLD.
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,}$", re.I
)
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_domain(raw: str) -> str:
    """Reduce a pasted URL to a bare host: strip scheme, path, query and port."""
    host = raw.strip().lower()
    host = host.split("://", 1)[-1]
    host = host.split("/", 1)[0]
    host = host.split("?", 1)[0]
    return host.split(":", 1)[0]


def valid_domain(raw: str) -> bool:
    return bool(_DOMAIN_RE.match(normalize_domain(raw)))


def valid_email(raw: str) -> bool:
    return bool(_EMAIL_RE.match(raw.strip()))
