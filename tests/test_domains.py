"""Registrable-domain helper, including the UK two-level suffixes."""

from app.acquisition.domains import registrable_domain


def test_plain_two_label_domain():
    assert registrable_domain("example.com") == "example.com"
    assert registrable_domain("www.example.com") == "example.com"
    assert registrable_domain("a.b.example.com") == "example.com"


def test_uk_second_level_suffix():
    assert registrable_domain("bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("news.bbc.co.uk") == "bbc.co.uk"
    assert registrable_domain("shop.example.org.uk") == "example.org.uk"


def test_empty_host():
    assert registrable_domain("") == ""
