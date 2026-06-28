"""Sitemap parsing, coverage and health logic for the technical-SEO audit."""

from app.acquisition.crawler import CrawledPage
from app.acquisition.fetcher import SitemapProbe, _parse_sitemap
from app.audits.technical_seo import _normalise_url, _sitemap_coverage, _sitemap_health


def test_parse_sitemap_urlset():
    xml = "<urlset><url><loc>https://x/a</loc></url><url><loc>https://x/b</loc></url></urlset>"
    is_index, locs = _parse_sitemap(xml)
    assert is_index is False
    assert locs == ["https://x/a", "https://x/b"]


def test_parse_sitemap_index_is_flagged():
    xml = "<sitemapindex><sitemap><loc>https://x/sm1.xml</loc></sitemap></sitemapindex>"
    is_index, locs = _parse_sitemap(xml)
    assert is_index is True
    assert locs == ["https://x/sm1.xml"]


def test_normalise_url_drops_scheme_and_trailing_slash():
    assert _normalise_url("https://x.com/about/") == _normalise_url("http://x.com/about")
    assert _normalise_url("https://x.com/") == "x.com/"
    assert _normalise_url("https://x.com/p?q=1") == "x.com/p?q=1"


def test_coverage_none_without_sitemap_or_pages():
    assert _sitemap_coverage([], [CrawledPage("https://x/a", 0, 200)]) is None
    assert _sitemap_coverage(["https://x/a"], []) is None


def test_coverage_full_and_partial():
    pages = [
        CrawledPage("https://x/a", 0, 200),
        CrawledPage("https://x/b", 1, 200),
        CrawledPage("https://x/c", 1, 200),
    ]
    full = _sitemap_coverage(["https://x/a", "https://x/b", "https://x/c"], pages)
    assert full.score == 100.0
    assert full.evidence == {}

    # Two of three crawled pages listed -> 0.67 coverage -> the middle band.
    partial = _sitemap_coverage(["https://x/a", "https://x/b"], pages)
    assert partial.score == 70.0
    assert partial.evidence["missing_from_sitemap"] == ["x/c"]

    # One of three -> 0.33 -> the low band.
    low = _sitemap_coverage(["https://x/a"], pages)
    assert low.score == 40.0


def test_coverage_ignores_non_200_pages():
    pages = [CrawledPage("https://x/a", 0, 200), CrawledPage("https://x/missing", 1, 404)]
    result = _sitemap_coverage(["https://x/a"], pages)
    assert result.score == 100.0


def test_health_all_live():
    sample = [SitemapProbe("https://x/a", 200, False), SitemapProbe("https://x/b", 200, False)]
    result = _sitemap_health(sample)
    assert result.score == 100.0
    assert result.status.value == "pass"


def test_health_counts_dead_and_redirects():
    sample = [
        SitemapProbe("https://x/a", 200, False),
        SitemapProbe("https://x/dead", 404, False),
        SitemapProbe("https://x/gone", None, False),
        SitemapProbe("https://x/moved", 200, True),
    ]
    result = _sitemap_health(sample)
    assert result.score == 50.0
    assert result.status.value == "fail"
    assert "https://x/dead" in result.evidence["dead"]
    assert "https://x/gone" in result.evidence["dead"]
    assert "https://x/moved" in result.evidence["redirecting"]


def test_health_none_without_sample():
    assert _sitemap_health([]) is None
