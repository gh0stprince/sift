"""Tests for answer synthesis."""
from sift.synthesize import build_context_from_snippets, synthesize_stream


def test_build_context_from_snippets():
    snippets = [
        {"url": "https://a.com", "title": "Page A", "body": "Content about A"},
        {"url": "https://b.com", "title": "Page B", "body": "Content about B"},
    ]
    context, source_text = build_context_from_snippets(snippets)
    assert "[1]" in context
    assert "Page A" in context
    # source_text is formatted string with URLs; verify exact URLs present
    assert source_text.count("https://a.com") == 1
    assert source_text.count("https://b.com") == 1
    assert len(context) > 0
    assert len(source_text) > 0


def test_build_context_empty():
    context, source_text = build_context_from_snippets([])
    assert context == ""
    assert source_text == ""


def test_synthesize_stream_no_key_fallback(monkeypatch):
    monkeypatch.setattr("sift.synthesize.DEFAULT_API_KEY", None)
    tokens = list(synthesize_stream("test", "context"))
    assert len(tokens) > 0
    assert "error" in tokens[0].lower()
