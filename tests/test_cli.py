"""CLI regression tests."""

from click.testing import CliRunner

from sift.cli import main


class _EmptyDB:
    """Minimal database for live-search CLI tests."""

    def __init__(self, *args, **kwargs) -> None:
        del args, kwargs

    @staticmethod
    def search(_query: str, limit: int = 10) -> list[dict]:
        del limit
        return []


def test_ask_live_prints_ranked_source_urls(monkeypatch) -> None:
    """Live synthesis must retain URLs when ranking snippet results."""
    monkeypatch.setattr("sift.db.DB", _EmptyDB)
    monkeypatch.setattr(
        "sift.pulse.PulseEngine._generate_query_variations",
        lambda _self, query: [query],
    )
    monkeypatch.setattr(
        "sift.pulse.PulseEngine._search_ddg",
        lambda _self, _query, max_results=10: [
            {
                "url": "https://example.com/source",
                "title": "Example Source",
                "body": "Grounded snippet.",
            }
        ][:max_results],
    )
    monkeypatch.setattr(
        "sift.synthesize.synthesize_stream",
        lambda _query, _context: iter(["Answer with citation [1]."]),
    )

    captured = {}

    def fake_write_raw_source(title, slug, query, synthesis, sources):
        captured.update(
            title=title,
            slug=slug,
            query=query,
            synthesis=synthesis,
            sources=sources,
        )
        return "C:/tmp/test-query.md"

    monkeypatch.setattr("sift.wiki.write_raw_source", fake_write_raw_source)

    result = CliRunner().invoke(main, ["ask", "test query", "--live", "--wiki"])

    assert result.exit_code == 0
    assert "Sources:" in result.output
    assert "[1] Example Source" in result.output
    assert "https://example.com/source" in result.output
    assert captured["sources"] == ["https://example.com/source"]
