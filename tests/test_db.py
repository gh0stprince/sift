import os
import tempfile
import pytest
from sift.db import DB


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db = DB(tmp.name)
    yield db
    db.close()
    os.unlink(tmp.name)


def test_create_db(db):
    """Verify all tables and triggers exist."""
    tables = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = [row["name"] for row in tables]
    assert "sources" in table_names
    assert "pages" in table_names
    assert "pages_fts" in table_names
    assert "pulses" in table_names

    triggers = db.conn.execute(
        "SELECT name FROM sqlite_master WHERE type='trigger' ORDER BY name"
    ).fetchall()
    trigger_names = [row["name"] for row in triggers]
    assert "pages_ai" in trigger_names
    assert "pages_ad" in trigger_names
    assert "pages_au" in trigger_names


def test_add_page_and_search(db):
    """Add two pages and search for content."""
    db.add_source("Test Feed", "http://example.com/feed.xml")
    source_id = 1

    db.add_page(
        "http://example.com/one",
        "First Article",
        "The quick brown fox jumps over the lazy dog",
        source_id=source_id,
    )
    db.add_page(
        "http://example.com/two",
        "Second Article",
        "Python is a great programming language for data science",
        source_id=source_id,
    )

    results = db.search("fox", limit=10)
    assert len(results) == 1
    assert results[0]["url"] == "http://example.com/one"
    assert "<b>" in results[0]["excerpt"]

    results = db.search("python", limit=10)
    assert len(results) == 1
    assert results[0]["url"] == "http://example.com/two"


def test_dedup_urls(db):
    """Same URL twice should update, not duplicate."""
    db.add_page("http://example.com/dup", "Original Title", "Original content")
    db.add_page("http://example.com/dup", "Updated Title", "Updated content")

    rows = db.conn.execute(
        "SELECT COUNT(*) AS cnt FROM pages WHERE url = ?",
        ("http://example.com/dup",),
    ).fetchone()
    assert rows["cnt"] == 1

    row = db.conn.execute(
        "SELECT title, content FROM pages WHERE url = ?",
        ("http://example.com/dup",),
    ).fetchone()
    assert row["title"] == "Updated Title"
    assert row["content"] == "Updated content"

    # FTS should reflect the updated content, not the original
    results = db.search("Updated", limit=10)
    assert len(results) == 1

    results = db.search("Original", limit=10)
    assert len(results) == 0


def test_stats(db):
    """Add pages and verify stats counts."""
    db.add_source("Feed A", "http://a.com/feed.xml", kind="feed")
    db.add_source("Feed B", "http://b.com/feed.xml", kind="feed")

    db.add_page("http://a.com/1", "A1", "Content A1", source_id=1)
    db.add_page("http://a.com/2", "A2", "Content A2", source_id=1)
    db.add_page("http://b.com/1", "B1", "Content B1", source_id=2)
    db.add_page("http://pulse.com/1", "Pulse1", "Pulse content", pulse_id=1)

    stats = db.get_stats()
    assert stats["total_pages"] == 4
    assert stats["total_sources"] == 2
    assert stats["total_pulses"] == 0  # No pulses inserted directly
    assert stats["pulse_pages"] == 1
    assert stats["feed_pages"] == 3
    assert stats["newest_page"] is not None


def test_search_fresh_boost():
    import tempfile
    from pathlib import Path
    from datetime import datetime, timedelta
    db = DB(db_path=Path(tempfile.mkstemp(suffix=".db")[1]))
    sid = db.add_source("test", "http://test.com/rss")

    # Insert two pages with same content but different fetch dates
    db.add_page("http://test.com/old", "Old Page", "mycelial networks fungus", sid)
    db.add_page("http://test.com/new", "New Page", "mycelial networks fungus", sid)

    # Manually set old page to 30 days ago
    old_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    db.conn.execute("UPDATE pages SET fetched_at=? WHERE url=?", (old_date, "http://test.com/old"))
    db.conn.commit()

    # Fresh search should rank the newer page first
    fresh_results = db.search("mycelial", fresh=True)
    assert len(fresh_results) >= 2
    assert fresh_results[0]["url"] == "http://test.com/new"

    # Non-fresh search (default) should use FTS5 rank order
    normal_results = db.search("mycelial")
    assert len(normal_results) >= 2

    db.close()
