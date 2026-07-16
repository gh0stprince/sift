"""CLI regression tests."""

from pathlib import Path

from click.testing import CliRunner

from sift.cli import main


def test_documented_cli_contract_matches_click_help() -> None:
    """Core documented commands and options must remain valid Click contracts."""
    runner = CliRunner()
    cases = {
        ("ask", "--help"): ("--limit", "--wiki", "--wiki-slug"),
        ("feeds", "--help"): ("{list|add|init}",),
        ("ingest", "--help"): ("--max-per-feed",),
        ("search", "--help"): ("--fresh",),
    }
    for arguments, expected in cases.items():
        result = runner.invoke(main, list(arguments))
        assert result.exit_code == 0, result.output
        for token in expected:
            assert token in result.output

    root = Path(__file__).resolve().parents[1]
    usage = (root / "docs" / "Usage.md").read_text(encoding="utf-8")
    faq = (root / "docs" / "FAQ.md").read_text(encoding="utf-8")
    readme = (root / "README.md").read_text(encoding="utf-8")
    assert "sift feed\n" not in usage
    assert "sift feed --limit" not in usage
    assert "~/.local/share/sift/sift.db" not in faq
    assert "--wiki llm-benchmarks-2024" not in readme


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
