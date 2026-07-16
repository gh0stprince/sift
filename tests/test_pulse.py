"""Tests for the pulse engine."""

from __future__ import annotations

import os
import tempfile

import pytest

from sift.db import DB
from sift.pulse import PulseEngine


class TestPulseEngine:
    """Unit tests for PulseEngine (no network)."""

    def test_query_variations(self) -> None:
        """Verify _generate_query_variations returns 8 variants including quoted."""
        engine = PulseEngine(db=None, user_agent="test")  # type: ignore[arg-type]
        variants = engine._generate_query_variations("mycelial networks")

        assert len(variants) == 8, f"Expected 8 variations, got {len(variants)}"
        assert "mycelial networks" in variants, "Base query should be present"
        assert '"mycelial networks"' in variants, "Quoted form should be present"
        assert "what is mycelial networks" in variants
        assert "mycelial networks research 2026" in variants
        assert "mycelial networks overview" in variants
        assert "mycelial networks explained" in variants
        assert "mycelial networks vs" in variants
        assert "mycelial networks how does it work" in variants


class _PulseDB:
    def add_pulse(self, _query: str, _depth: int) -> int:
        return 42

    def finish_pulse(self, _pulse_id: int, _pages_found: int) -> None:
        """Satisfy the persistence boundary without a real database."""


def _offline_engine(monkeypatch, seeds, graph):
    engine = PulseEngine(
        db=_PulseDB(), user_agent="test", sleeper=lambda _delay: None
    )
    calls = []
    monkeypatch.setattr(engine, "_generate_query_variations", lambda _query: ["q"])
    monkeypatch.setattr(
        engine,
        "_search_ddg",
        lambda _query, max_results=10: [
            {"url": url, "title": url, "body": "snippet"}
            for url in seeds[:max_results]
        ],
    )

    def fetch(url, pulse_id, link_depth):
        calls.append((url, pulse_id, link_depth))
        return len(calls), graph.get(url, [])

    monkeypatch.setattr(engine, "_fetch_page", fetch)
    return engine, calls


def test_depth_zero_is_search_only(monkeypatch) -> None:
    engine, calls = _offline_engine(
        monkeypatch, ["https://a.example/"], {"https://a.example/": []}
    )

    result = engine.run("query", depth=0, max_pages=5)

    assert result["pulse_id"] == 42
    assert result["pages_found"] == 0
    assert result["urls_discovered"] == 1
    assert not calls


def test_depth_two_follows_one_link_level_with_cycle_dedup(monkeypatch) -> None:
    engine, calls = _offline_engine(
        monkeypatch,
        ["https://a.example/", "https://a.example/#fragment"],
        {
            "https://a.example/": [
                "https://b.example/page",
                "https://b.example/page#section",
            ],
            "https://b.example/page": ["https://a.example/"],
        },
    )

    result = engine.run("query", depth=2, max_pages=10)

    assert calls == [
        ("https://a.example/", 42, 0),
        ("https://b.example/page", 42, 1),
    ]
    assert result["pages_found"] == 2
    assert result["urls_discovered"] == 2
    assert result["total_depth"] == 2


def test_max_pages_is_one_global_budget_across_depths(monkeypatch) -> None:
    engine, calls = _offline_engine(
        monkeypatch,
        ["https://a.example/", "https://b.example/", "https://c.example/"],
        {
            "https://a.example/": ["https://d.example/"],
            "https://b.example/": ["https://e.example/"],
        },
    )

    result = engine.run("query", depth=3, max_pages=2)

    assert len(calls) == 2
    assert result["pages_found"] == 2


@pytest.mark.integration
def test_real_pulse() -> None:
    """Run a real pulse against DDG and verify results in the DB.

    This test hits real DuckDuckGo search and real web pages, so it
    requires network access and may take 30-60 seconds.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = DB(tmp.name)
    try:
        # DDG aggressively rate-limits; retry up to 3 times with backoff
        import time as _time

        result = None
        for attempt in range(3):
            engine = PulseEngine(db, user_agent="Sift-Test/0.1.0")
            result = engine.run("mycelial networks", depth=1, max_pages=5)
            if result["pages_found"] > 0:
                break
            if attempt < 2:
                _time.sleep(5 * (attempt + 1))

        # Verify pulse record
        assert result is not None
        assert result["pulse_id"] is not None
        assert result["pages_found"] > 0, (
            "Expected at least one page found "
            "(DDG rate limiting may cause transient failures)"
        )
        assert result["total_depth"] == 1

        # Verify pages exist in DB with matching pulse_id
        rows = db.conn.execute(
            "SELECT COUNT(*) AS cnt FROM pages WHERE pulse_id = ?",
            (result["pulse_id"],),
        ).fetchone()
        assert rows["cnt"] > 0
        assert rows["cnt"] == result["pages_found"]

        # Verify FTS search for keyword returns results
        search_results = db.search("mycelial", limit=10)
        assert len(search_results) > 0, (
            "FTS search for 'mycelial' should return at least one result"
        )

    finally:
        db.close()
        os.unlink(tmp.name)
