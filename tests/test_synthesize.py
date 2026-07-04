def test_build_context_from_snippets():
    from sift.synthesize import build_context_from_snippets
    snippets = [
        {"url": "https://a.com", "title": "Page A", "body": "Content about A here"},
        {"url": "https://b.com", "title": "Page B", "body": "Content about B here"},
    ]
    context, sources = build_context_from_snippets(snippets)
    assert "[1]" in context
    assert "[2]" in context
    assert "Content about A" in context
    assert "https://a.com" in sources
    assert "Page B" in context
