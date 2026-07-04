"""Tests for answer synthesis."""
from sift.synthesize import build_context_from_snippets, synthesize_stream


def test_build_context_from_snippets():
    snippets = [
        {"url": "https://a.com", "title": "Page A", "body": "Content about A"},
        {"url": "https://b.com", "title": "Page B", "body": "Content about B"},
    ]
    context, sources = build_context_from_snippets(snippets)
    assert "[1]" in context
    assert "Page A" in context
    assert "https://a.com" in sources
    assert len(context) > 0
    assert len(sources) > 0


def test_build_context_empty():
    context, sources = build_context_from_snippets([])
    assert context == ""
    assert sources == ""
