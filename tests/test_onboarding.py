"""Onboarding input validation."""

from __future__ import annotations

from app.onboarding import normalize_domain, valid_domain, valid_email


def test_normalize_domain_strips_scheme_path_query_port():
    assert normalize_domain("https://Example.com/path?x=1") == "example.com"
    assert normalize_domain("http://sub.example.co.uk:8080/") == "sub.example.co.uk"
    assert normalize_domain("  Example.COM  ") == "example.com"


def test_valid_domain_accepts_real_hosts():
    assert valid_domain("example.com")
    assert valid_domain("https://www.example.co.uk/pricing")
    assert valid_domain("sub.domain.example.io")


def test_valid_domain_rejects_junk():
    assert not valid_domain("not a url")
    assert not valid_domain("localhost")  # no TLD
    assert not valid_domain("someone@example.com")  # an email, not a URL
    assert not valid_domain("")
    assert not valid_domain("http://")


def test_valid_email():
    assert valid_email("you@company.com")
    assert valid_email(" Person@sub.example.co.uk ")
    assert not valid_email("nope")
    assert not valid_email("a@b")  # no TLD
    assert not valid_email("a b@c.com")
