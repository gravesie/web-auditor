"""Parsing helpers across the SEO, schema and GEO audits."""

from app.audits.geo import _ai_crawler_blocks
from app.audits.schema import _ORG_TYPES, _collect_types, _find_objects, _parse_jsonld
from app.audits.technical_seo import _blocks_everything, _canonical_href, _meta_content


def test_robots_blocks_everything():
    assert _blocks_everything("User-agent: *\nDisallow: /")
    assert not _blocks_everything("User-agent: *\nDisallow: /admin")
    assert not _blocks_everything("")


def test_meta_content_is_attribute_order_independent():
    assert _meta_content('<meta name="robots" content="noindex">', "robots") == "noindex"
    assert _meta_content('<meta content="noindex" name="robots">', "robots") == "noindex"
    assert _meta_content('<meta name="viewport" content="w">', "robots") is None


def test_canonical_href_is_attribute_order_independent():
    assert _canonical_href('<link rel="canonical" href="https://x/">') == "https://x/"
    assert _canonical_href('<link href="https://x/" rel="canonical">') == "https://x/"
    assert _canonical_href('<link rel="stylesheet" href="a.css">') is None


def test_jsonld_parse_and_types():
    html = '<script type="application/ld+json">{"@type":"Organization","name":"X"}</script>'
    blocks, invalid = _parse_jsonld(html)
    assert invalid == 0
    assert len(blocks) == 1
    assert "Organization" in _collect_types(blocks)


def test_jsonld_invalid_is_counted():
    blocks, invalid = _parse_jsonld('<script type="application/ld+json">{not json}</script>')
    assert blocks == []
    assert invalid == 1


def test_organization_subtype_recognised():
    html = (
        '<script type="application/ld+json">'
        '{"@type":"NewsMediaOrganization","name":"BBC","logo":"x"}</script>'
    )
    blocks, _ = _parse_jsonld(html)
    orgs = _find_objects(blocks, _ORG_TYPES)
    assert orgs and orgs[0]["name"] == "BBC"


def test_ai_crawler_block_detection():
    assert "gptbot" in _ai_crawler_blocks("User-agent: GPTBot\nDisallow: /")
    assert _ai_crawler_blocks("User-agent: *\nDisallow: /admin") == set()
