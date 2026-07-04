"""Integration tests for DomainCrawler.

Tests are real-URL-based (sitemaps.org) so they exercise actual HTTP
fetching.  They use a lightweight in-memory stub instead of the real DB.
"""

from __future__ import annotations

from typing import Any

import pytest

from sift.crawler import DomainCrawler


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
        assert result == "https://example.com"

    def test_get_root_invalid(self) -> None:
        """Invalid URL should return empty string."""
        result = DomainCrawler._get_root("not-a-url")
        assert result == ""


class TestDiscoverSitemapsReal:
    """Actually hit sitemaps.org and verify sitemap discovery."""

    def test_discover_sitemaps_real(self, crawler: DomainCrawler) -> None:
        """Hit https://www.sitemaps.org and verify at least one sitemap URL is found."""
        root = DomainCrawler._get_root("https://www.sitemaps.org")
        assert root == "https://www.sitemaps.org", "Root extraction should work"

        sitemaps = crawler._discover_sitemaps(root)
        assert len(sitemaps) > 0, "Expected at least one sitemap URL from sitemaps.org"
        for sm in sitemaps:
            assert sm.startswith("http"), f"Sitemap URL should start with http: {sm}"
