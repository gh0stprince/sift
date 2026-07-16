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
    assert "  - https://example.com/first" in frontmatter
    assert "  - https://example.com/second?part=2#details" in frontmatter

    capture = read_capture(path)
    assert capture.metadata["source_urls"] == urls
