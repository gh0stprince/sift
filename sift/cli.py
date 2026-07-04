"""Sift CLI — search, pulse, feed, and ask commands for AI-powered web research."""

from __future__ import annotations

from pathlib import Path

import click

DEFAULT_FEEDS = [
    ("Lobsters", "https://lobste.rs/rss"),
    ("Hacker News", "https://hnrss.org/frontpage"),
    ("ArXiv CS.AI", "http://export.arxiv.org/rss/cs.AI"),
    ("ArXiv CS.LG", "http://export.arxiv.org/rss/cs.LG"),
    ("ArXiv q-bio.NC", "http://export.arxiv.org/rss/q-bio.NC"),
    ("LessWrong", "https://www.lesswrong.com/feed.xml"),
    ("Astral Codex Ten", "https://www.astralcodexten.com/feed"),
]


# ---------------------------------------------------------------------------
#  Group entry point
# ---------------------------------------------------------------------------


@click.group(invoke_without_command=True)
@click.option("--db", "-d", type=click.Path(), default=None, help="Path to sift database")
@click.pass_context
def main(ctx, db):
    """Sift — AI-powered web research tool.

    Search, explore, and analyze web content through RSS feeds,
    recursive research pulses, and full-text search.
    """
    from sift.db import DB

    resolved = Path(db).resolve() if db else None
    ctx.ensure_object(dict)
    try:
        ctx.obj["db"] = DB(db_path=resolved)
    except Exception as exc:
        click.secho(f"Error initializing database: {exc}", fg="red", err=True)
        ctx.exit(1)

    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---------------------------------------------------------------------------
#  Commands
# ---------------------------------------------------------------------------


@main.command()
@click.argument("query")
@click.option("--limit", default=10, type=int, help="Max results to return")
@click.pass_context
def search(ctx, query, limit):
    """Search indexed content (pages from feeds and pulses)."""
    db = ctx.obj["db"]
    results = db.search(query, limit=limit)

    if not results:
        click.echo("No results found. Try `sift pulse <query>` to discover content.")
        return

    for r in results:
        title = r.get("title") or "(no title)"
        url = r.get("url") or ""
        link_depth = r.get("link_depth", 0)
        fetched = r.get("fetched_at", "")[:10]
        excerpt = r.get("excerpt") or ""

        click.echo()
        click.secho(title, bold=True)
        click.secho(url, fg="blue")
        click.echo(f"  depth={link_depth}  fetched={fetched}")
        if excerpt:
            click.echo(f"  {excerpt}")


@main.command()
@click.argument("query")
@click.option("--depth", default=2, type=int, help="Link-following depth")
@click.option("--max-pages", default=30, type=int, help="Max pages to fetch")
@click.pass_context
def pulse(ctx, query, depth, max_pages):
    """Run a research pulse: recursively discover content from a query."""
    from sift.pulse import PulseEngine

    db = ctx.obj["db"]
    engine = PulseEngine(db)
    click.echo(
        f"🔍 Running pulse for '{query}'"
        f" (depth={depth}, max_pages={max_pages})..."
    )
    result = engine.run(query, depth=depth, max_pages=max_pages)
    click.echo(
        f"✅ Pulse complete — {result.get('pages_found', 0)} pages found"
        f" (pulse_id={result.get('pulse_id')})"
    )


@main.command()
@click.argument("action", type=click.Choice(["list", "add", "init"]))
@click.argument("args", nargs=-1)
@click.pass_context
def feeds(ctx, action, args):
    """Manage RSS/Atom feed sources."""
    from sift.feeds import FeedFetcher

    db = ctx.obj["db"]
    fetcher = FeedFetcher(db)

    if action == "list":
        sources = fetcher.list_feeds()
        if not sources:
            click.echo("No feeds registered. Run `sift feeds init` to add defaults.")
            return
        for s in sources:
            click.echo(f"{s['id']:>3}  {s['name']:<25}  {s['feed_url']}")

    elif action == "init":
        count = 0
        for name, url in DEFAULT_FEEDS:
            try:
                fetcher.add_feed(name, url)
                count += 1
            except Exception:
                click.echo(f"  ✗ {name} — already exists or failed")
        click.echo(f"Added {count} default feed(s).")

    elif action == "add":
        if len(args) < 2:
            click.echo("Usage: sift feeds add <name> <url>", err=True)
            return
        name, url = args[0], args[1]
        fetcher.add_feed(name, url)
        click.echo(f"Added feed '{name}' → {url}")


@main.command()
@click.option("--max-per-feed", default=10, type=int, help="Max entries per feed")
@click.pass_context
def ingest(ctx, max_per_feed):
    """Fetch and index pages from all registered feeds."""
    from sift.feeds import FeedFetcher

    db = ctx.obj["db"]
    fetcher = FeedFetcher(db)
    click.echo(f"📡 Ingesting up to {max_per_feed} entries per feed...")
    stats = fetcher.run_all(max_per_feed=max_per_feed)
    click.echo(
        f"✅ Done — feeds_checked={stats['feeds_checked']},"
        f" pages_fetched={stats['pages_fetched']},"
        f" pages_skipped={stats['pages_skipped']},"
        f" errors={stats['errors']}"
    )


@main.command()
@click.pass_context
def stats(ctx):
    """Show index statistics."""
    db = ctx.obj["db"]
    s = db.get_stats()

    click.secho("📊 Sift Index Stats", bold=True)
    click.echo(f"  total_pages:    {s['total_pages']}")
    click.echo(f"  feed_pages:     {s['feed_pages']}")
    click.echo(f"  pulse_pages:    {s['pulse_pages']}")
    click.echo(f"  feeds_tracked:  {s['total_sources']}")
    click.echo(f"  total_pulses:   {s['total_pulses']}")
    click.echo(f"  newest_page:    {s['newest_page'] or 'never'}")


@main.command()
@click.argument("query")
@click.option("--limit", default=10, type=int, help="Max search results")
@click.option(
    "--no-llm",
    is_flag=True,
    help="Skip LLM synthesis — show raw results only",
)
@click.option("--live", is_flag=True, help="Answer from search snippets (faster, no page storage)")
@click.pass_context
def ask(ctx, query, limit, no_llm, live):
    """Ask a question — search index, pulse if empty, synthesize answer with citations."""
    from sift.pulse import PulseEngine
    from sift.synthesize import synthesize, build_context

    db = ctx.obj["db"]

    # First pass: search existing index
    results = db.search(query, limit=limit)

    # --live flag: if no results in index, search live via DDG
    if live and not results:
        click.echo("No results in index. Searching live...")
        from sift.pulse import PulseEngine
        from sift.synthesize import build_context_from_snippets
        engine = PulseEngine(ctx.obj["db"])
        variations = engine._generate_query_variations(query)

        all_urls = {}
        for v in variations:
            for r in engine._search_ddg(v, max_results=10):
                u = r.get("url", "")
                if not u:
                    continue
                if u not in all_urls:
                    all_urls[u] = {"count": 0, "title": r["title"], "body": r["body"]}
                all_urls[u]["count"] += 1

        ranked = sorted(all_urls.items(), key=lambda x: -x[1]["count"])
        snippet_results = [info for url, info in ranked[:limit]]

        if not snippet_results:
            click.echo("No live results found. Try a broader query.")
            return

        context, source_text = build_context_from_snippets(snippet_results)
        click.secho("Synthesizing from live search snippets...", dim=True)
        answer = synthesize(query, context)

        if answer.startswith("[Synthesis error]"):
            click.secho(answer, fg="red")
            return

        click.echo(f"\n{answer}\n")
        click.secho("Sources (live search):", bold=True)
        click.echo(source_text)
        return

    if not results:
        click.echo("No results in index. Running a quick pulse...")
        engine = PulseEngine(db)
        engine.run(query, depth=1, max_pages=10)
        results = db.search(query, limit=limit)

    if not results:
        click.echo(
            "Still no results found."
            " Try a broader query or `sift ingest` first."
        )
        return

    click.echo(f"\nFound {len(results)} source(s).")

    if no_llm:
        # Raw mode — show excerpts
        click.secho("\n[Raw search results — --no-llm]\n", dim=True)
        for r in results[:limit]:
            title = r.get("title") or "(no title)"
            url = r.get("url") or ""
            excerpt = r.get("excerpt") or ""
            click.echo(click.style(title, bold=True))
            click.echo(click.style(url, fg="blue"))
            if excerpt:
                click.echo(f"  {excerpt}")
            click.echo()
        return

    # LLM synthesis mode
    context, source_text = build_context(results, limit=limit)

    click.echo()
    click.secho("🤔 Synthesizing answer...", dim=True)

    answer = synthesize(query, context)

    click.echo()

    # Check if synthesis returned an error
    if answer.startswith("[Synthesis error]"):
        click.secho(answer, fg="red")
        click.echo()
        click.secho("Showing raw search results instead:\n", dim=True)
        for r in results[:3]:
            title = r.get("title") or "(no title)"
            url = r.get("url") or ""
            excerpt = r.get("excerpt") or ""
            click.echo(click.style(title, bold=True))
            click.echo(click.style(url, fg="blue"))
            if excerpt:
                click.echo(f"  {excerpt}")
            click.echo()
        return

    # Print answer
    click.echo(answer)
    click.echo()

    # Print sources
    click.secho("Sources:", bold=True)
    click.echo(source_text)


if __name__ == "__main__":
    main()
