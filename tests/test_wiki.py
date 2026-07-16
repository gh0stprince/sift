"""Tests for Obsidian-compatible wiki output."""

from pathlib import Path

from sift.curation import read_capture
from sift.wiki import write_raw_source


def test_write_raw_source_uses_flat_obsidian_source_property(
    monkeypatch, tmp_path: Path
) -> None:
    """Raw captures expose URL scalars instead of nested property objects."""
    monkeypatch.setattr("sift.wiki.WIKI_RAW_DIR", tmp_path)
    urls = [
        "https://example.com/first",
        "https://example.com/second?part=2#details",
    ]

    path = Path(
        write_raw_source(
            "Example",
            "example",
            "example query",
            "Grounded answer [1] [2].",
            urls,
        )
    )
    text = path.read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]

    assert "source:\n" in frontmatter
    assert "sources:\n" not in frontmatter
    assert "  - url:" not in frontmatter
    assert '  - "https://example.com/first"' in frontmatter
    assert '  - "https://example.com/second?part=2#details"' in frontmatter

    capture = read_capture(path)
    assert capture.metadata["source_urls"] == urls


def test_write_raw_source_appends_once_without_corrupting_frontmatter(
    monkeypatch, tmp_path: Path
) -> None:
    """A repeated slug keeps one valid frontmatter block and one copy per answer."""
    monkeypatch.setattr("sift.wiki.WIKI_RAW_DIR", tmp_path)

    path = Path(write_raw_source(
        "Repeated", "repeated", "first query", "First answer\n\n---\n\nBody rule.", []
    ))
    write_raw_source(
        "Repeated", "repeated", "second query", "Second answer\n\n---\n\nAnother rule.", []
    )

    text = path.read_text(encoding="utf-8")
    capture = read_capture(path)
    frontmatter = text.split("\n---\n", 1)[0]

    assert frontmatter.count("updated:") == 1
    assert capture.metadata["title"] == "Repeated"
    assert text.count("First answer") == 1
    assert text.count("Second answer") == 1
    assert text.count("## Update -") == 1
    assert not list(tmp_path.glob(".repeated.md.*"))


def test_repeated_slug_merges_query_and_source_provenance(
    monkeypatch, tmp_path: Path
) -> None:
    """Every appended update remains discoverable by downstream curation."""
    monkeypatch.setattr("sift.wiki.WIKI_RAW_DIR", tmp_path)

    path = Path(
        write_raw_source(
            "Repeated",
            "repeated",
            "first query",
            "First answer.",
            ["https://example.com/first"],
        )
    )
    second_query = 'second: "quoted" \\ path\nnext line'
    write_raw_source(
        "Repeated",
        "repeated",
        second_query,
        "Second answer.",
        ["https://example.com/second"],
    )

    capture = read_capture(path)

    assert capture.metadata["source_query"] == ["first query", second_query]
    assert capture.metadata["source_urls"] == [
        "https://example.com/first",
        "https://example.com/second",
    ]


def test_write_raw_source_round_trips_yaml_scalars(monkeypatch, tmp_path: Path) -> None:
    """Quotes, colons, slashes, Unicode and newlines remain scalar values."""
    monkeypatch.setattr("sift.wiki.WIKI_RAW_DIR", tmp_path)
    title = 'A "quoted": title \\ with café\nand a second line'
    query = 'why: "this" \\ path?\nsecond line Ω'
    urls = ['https://example.com/a:b?quote="yes"\\path', "https://例.example/資料"]

    path = Path(write_raw_source(title, "hostile", query, "Safe answer.", urls))
    capture = read_capture(path)

    assert capture.metadata["title"] == title
    assert capture.metadata["source_query"] == query
    assert capture.metadata["source_urls"] == urls
