"""Integration tests for the feed fetcher.

These tests hit real URLs (lobste.rs, httpbin.org) and are therefore
slower than pure unit tests.  They use a lightweight in-memory stub
instead of the real DB so they don't require a working ``sift.db``
module.
"""

from __future__ import annotations

from typing import Any

import pytest

from sift.feeds import FeedFetcher


# ---------------------------------------------------------------------------
# Fake database (in-memory stub)
# ---------------------------------------------------------------------------


class FakeDB:
    """Minimal in-memory DB stub that matches the interface ``FeedFetcher``
    expects (``get_sources``, ``add_source``, ``conn``, ``add_page``)."""

    def __init__(self) -> None:
        self.sources: list[dict[str, Any]] = []
        self.pages: dict[str, dict[str, str]] = {}

        # Provide a simple ``conn`` object so that
        # ``self.db.conn.execute(...)`` works inside ``FeedFetcher.run_all``.
        class _Conn:
            @staticmethod
            def execute(sql: str, params: tuple = ()) -> _Cursor:
                return _Cursor(sql, params)

        class _Cursor:
            def __init__(self, sql: str, params: tuple) -> None:
                self._sql = sql
                self._params = params

            @staticmethod
            def fetchone():  # type: ignore[no-untyped-def]
                return None  # always "URL not found"

        self.conn = _Conn()

    def get_sources(self) -> list[dict[str, Any]]:
        return self.sources

    def add_source(self, name: str, feed_url: str, kind: str = "feed") -> None:
        self.sources.append(
            {
                "id": len(self.sources) + 1,
                "name": name,
                "feed_url": feed_url,
                "kind": kind,
            }
        )

    def add_page(
        self,
        url: str,
        title: str,
        content: str,
        source_id: int | None = None,
    ) -> None:
        self.pages[url] = {
            "url": url,
            "title": title,
            "content": content,
            "source_id": source_id,
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fetcher() -> FeedFetcher:
    return FeedFetcher(FakeDB())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestFetchRealFeed:
    """Actually fetch a live RSS feed from lobste.rs."""

    def test_fetch_real_feed(self, fetcher: FeedFetcher) -> None:
        """Hit lobste.rs RSS and verify we get entries with url and title."""
        entries = fetcher.fetch_feed("https://lobste.rs/rss")
        assert len(entries) > 0, "Expected at least one entry from lobste.rs"
        for entry in entries:
            assert "url" in entry, "Each entry must have a URL"
            assert "title" in entry, "Each entry must have a title"
            assert entry["url"], "Entry URL should be non-empty"
            assert entry["title"], "Entry title should be non-empty"


class TestFetchRealPage:
    """Actually download a page and run trafilatura extraction."""

    def test_fetch_real_page(self, fetcher: FeedFetcher) -> None:
        """Hit example.com and verify extracted text is returned."""
        result = fetcher.fetch_page("https://example.com")
        assert result is not None, "Expected page data, got None"
        assert "url" in result, "Result must have a URL"
        assert "title" in result, "Result must have a title"
        assert "content" in result, "Result must have content"
        assert result["url"] == "https://example.com"
        # example.com contains "Example Domain" and "Learn more"
        assert len(result["content"]) > 0, "Extracted content should not be empty"


class TestRunAllIntegration:
    """End-to-end: add feeds, run_all, check stats."""

    def test_run_all_integration(self) -> None:
        """Add a real feed, run with max_per_feed=3, verify stats shape."""
        db = FakeDB()
        db.add_source("lobste.rs", "https://lobste.rs/rss", kind="feed")
        fetcher = FeedFetcher(db)

        stats = fetcher.run_all(max_per_feed=3)

        # Check that all expected keys are present
        assert "feeds_checked" in stats
        assert "pages_fetched" in stats
        assert "pages_skipped" in stats
        assert "errors" in stats

        # At least one feed was checked
        assert stats["feeds_checked"] >= 1

        # The sum of fetched + skipped + errors should equal the number
        # of entries we tried to process (up to max_per_feed)
        total_processed = stats["pages_fetched"] + stats["pages_skipped"] + stats["errors"]
        assert total_processed <= 3, "Should process at most max_per_feed entries"
        assert total_processed >= 1, "Should have processed at least one entry"

        # Verify that fetched pages were actually stored in the DB
        assert len(db.pages) == stats["pages_fetched"]

    def test_run_all_preserves_feed_source_id(self, monkeypatch) -> None:
        """Pages ingested from a feed retain that feed's database ID."""
        db = FakeDB()
        db.add_source("Example Feed", "https://example.com/feed.xml", kind="feed")
        fetcher = FeedFetcher(db)
        monkeypatch.setattr(
            fetcher,
            "fetch_feed",
            lambda _url: [{"url": "https://example.com/post", "title": "Post"}],
        )
        monkeypatch.setattr(
            fetcher,
            "fetch_page",
            lambda url: {"url": url, "title": "Post", "content": "Body"},
        )
        monkeypatch.setattr("sift.feeds.time.sleep", lambda _seconds: None)

        stats = fetcher.run_all(max_per_feed=1)

        assert stats["pages_fetched"] == 1
        assert db.pages["https://example.com/post"]["source_id"] == 1
