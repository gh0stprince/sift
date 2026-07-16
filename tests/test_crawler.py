"""Integration tests for DomainCrawler.

Tests are real-URL-based (sitemaps.org) so they exercise actual HTTP
fetching.  They use a lightweight in-memory stub instead of the real DB.
"""

from __future__ import annotations

from typing import Any

import pytest

from sift.crawler import DomainCrawler
from sift.db import DB
from sift.outbound import OutboundPolicy
from sift.robots import RobotsPolicy


# ---------------------------------------------------------------------------
# Fake database (in-memory stub)
# ---------------------------------------------------------------------------


class FakeDB:
    """Minimal in-memory DB stub matching the interface ``DomainCrawler``
    expects (``add_source``, ``add_page``)."""

    def __init__(self) -> None:
        self.sources: list[dict[str, Any]] = []
        self.pages: dict[str, dict[str, str]] = {}

    def add_source(self, name: str, feed_url: str, kind: str = "feed") -> int:
        idx = len(self.sources) + 1
        self.sources.append(
            {"id": idx, "name": name, "feed_url": feed_url, "kind": kind}
        )
        return idx

    def add_page(
        self,
        url: str,
        title: str,
        content: str,
        source_id: int | None = None,
        pulse_id: int | None = None,
        link_depth: int = 0,
    ) -> int:
        idx = len(self.pages) + 1
        self.pages[url] = {
            "id": idx,
            "url": url,
            "title": title,
            "content": content,
            "source_id": source_id,
            "pulse_id": pulse_id,
            "link_depth": link_depth,
        }
        return idx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def crawler() -> DomainCrawler:
    return DomainCrawler(FakeDB())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGetRoot:
    """Validate root URL extraction."""

    def test_get_root(self) -> None:
        """Verify ``_get_root`` returns ``scheme://hostname``."""
        result = DomainCrawler._get_root("https://www.sitemaps.org/faq")
        assert result == "https://www.sitemaps.org"

    def test_get_root_no_path(self) -> None:
        """Root extraction with no path should still work."""
        result = DomainCrawler._get_root("https://example.com")
        assert result == "https://example.com"

    def test_get_root_with_port(self) -> None:
        """Root extraction with port number."""
        result = DomainCrawler._get_root("https://example.com:8080/page")
        assert result == "https://example.com:8080"

    def test_get_root_invalid(self) -> None:
        """Invalid URL should return empty string."""
        result = DomainCrawler._get_root("not-a-url")
        assert result == ""

    def test_get_root_rejects_non_http_urls(self) -> None:
        """Crawling is limited to web URLs, never file or custom schemes."""
        assert DomainCrawler._get_root("file:///tmp/site") == ""

    def test_internal_url_requires_same_host(self) -> None:
        root = "https://example.com"
        assert DomainCrawler._is_internal_url("https://example.com/page", root)
        assert not DomainCrawler._is_internal_url("https://example.com.evil/page", root)
        assert not DomainCrawler._is_internal_url("file:///etc/passwd", root)


class _RobotsResponse:
    """Minimal deterministic response for sitemap discovery tests."""

    text = "User-agent: *\nAllow: /\nSitemap: https://example.com/map.xml\n"

    def raise_for_status(self) -> None:
        """Match the requests response API used by the policy."""


class _RobotsSession:
    """Session stub that records robots requests without live network I/O."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> _RobotsResponse:
        self.calls.append(url)
        return _RobotsResponse()


class TestDiscoverSitemaps:
    """Sitemap discovery must remain deterministic and robots-aware."""

    def test_discover_sitemaps_from_robots(self, crawler: DomainCrawler) -> None:
        """Read Sitemap directives without making a live network request."""
        session = _RobotsSession()
        crawler.session = session  # type: ignore[assignment]
        crawler.robots = RobotsPolicy(
            session,
            "Sift-Test/0.1",
            url_policy=OutboundPolicy(resolver=lambda _host: ["93.184.216.34"]),
        )
        root = "https://example.com"
        sitemaps = crawler._discover_sitemaps(root)
        assert sitemaps == ["https://example.com/map.xml"]
        assert session.calls == ["https://example.com/robots.txt"]


def test_repeat_crawl_reuses_real_database_source(monkeypatch, tmp_path) -> None:
    """Running one origin twice is a refresh, not a uniqueness crash."""
    database = DB(tmp_path / "sift.db")
    crawler = DomainCrawler(database)
    monkeypatch.setattr(crawler, "_discover_sitemaps", lambda _root: [])
    monkeypatch.setattr(crawler, "_crawl_from_root", lambda _root, max_pages: [])

    try:
        first = crawler.run("https://Example.COM/path", max_pages=1)
        second = crawler.run("https://example.com/other", max_pages=1)
    finally:
        crawler.close()
        database.close()

    assert second["source_id"] == first["source_id"]


class _BoundaryResponse:
    def __init__(self, *, text="", content=b"", status_code=200, location=None):
        self.text = text
        self.content = content
        self.status_code = status_code
        self.headers = {"Location": location} if location else {}

    def raise_for_status(self):
        """All configured boundary responses are non-error HTTP responses."""

    def close(self):
        """Match the response cleanup contract."""


class _BoundarySession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, **_kwargs):
        self.calls.append(url)
        return next(self.responses)

    def close(self):
        """Match the session cleanup contract."""


def test_crawl_redirect_cannot_leave_root_host() -> None:
    session = _BoundarySession([
        _BoundaryResponse(text="User-agent: *\nAllow: /\n"),
        _BoundaryResponse(
            status_code=302,
            location="https://other.example/private-boundary",
        ),
    ])
    crawler = DomainCrawler(
        FakeDB(),
        session=session,
        resolver=lambda _host: ["93.184.216.34"],
    )

    assert not crawler._crawl_from_root("https://example.com", max_pages=1)
    assert session.calls == [
        "https://example.com/robots.txt",
        "https://example.com",
    ]


def test_nested_sitemap_cannot_leave_root_host() -> None:
    xml = b"""<?xml version='1.0'?>
    <sitemapindex><sitemap><loc>https://other.example/map.xml</loc></sitemap></sitemapindex>
    """
    session = _BoundarySession([
        _BoundaryResponse(text="User-agent: *\nAllow: /\n"),
        _BoundaryResponse(content=xml),
    ])
    crawler = DomainCrawler(
        FakeDB(),
        session=session,
        resolver=lambda _host: ["93.184.216.34"],
    )

    assert not crawler._parse_sitemap(
        "https://example.com/index.xml", root="https://example.com"
    )
    assert session.calls == [
        "https://example.com/robots.txt",
        "https://example.com/index.xml",
    ]
