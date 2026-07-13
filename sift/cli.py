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
@click.option(
    "--encrypted", is_flag=True,
    help="Open the database with SQLCipher using SIFT_DB_KEY (never falls back)",
)
@click.pass_context
def main(ctx, db, encrypted):
    """Sift — AI-powered web research tool.

    Search, explore, and analyze web content through RSS feeds,
    recursive research pulses, and full-text search.
    \\b
    First time? Run: sift feeds init && sift ingest
    Then try: sift ask "your research question"
    """
    ctx.ensure_object(dict)
    if ctx.invoked_subcommand == "curate":
        ctx.obj["db"] = None
        return
    from sift.db import DB

    resolved = Path(db).resolve() if db else None
    try:
        ctx.obj["db"] = DB(db_path=resolved, encrypted=encrypted)
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
@click.option("--limit", default=10, type=click.IntRange(min=1), help="Max results to return")
@click.option("--fresh", is_flag=True, help="Boost recent results")
@click.pass_context
def search(ctx, query, limit, fresh):
    """Search indexed content (pages from feeds and pulses)."""
    db = ctx.obj["db"]
    results = db.search(query, limit=limit, fresh=fresh)

    if not results:
        click.secho("No results found. Try ", nl=False)
        click.secho("`sift pulse <query>`", bold=True, nl=False)
        click.secho(" to discover content.")
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
@click.option("--depth", default=2, type=click.IntRange(min=0), help="Link-following depth")
@click.option("--max-pages", default=30, type=click.IntRange(min=1), help="Max pages to fetch")
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
    click.echo(
        f"   Robots skipped: {result.get('robots_skipped', 0)}"
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
            click.secho("No feeds registered. Run ", nl=False)
            click.secho("`sift feeds init`", bold=True, nl=False)
            click.secho(" to add defaults.")
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
@click.option("--max-per-feed", default=10, type=click.IntRange(min=1), help="Max entries per feed")
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
@click.option("--raw-dir", type=click.Path(path_type=Path, exists=True, file_okay=False),
              default=None, help="Raw query directory (default: ~/llm-wiki/raw/queries)")
@click.option("--file", "raw_file", type=click.Path(path_type=Path, exists=True, dir_okay=False),
              default=None, help="Curate exactly one raw Markdown capture")
@click.option("--vault", type=click.Path(path_type=Path, file_okay=False),
              default=None, help="Wiki vault root (default: ~/llm-wiki)")
@click.option("--dry-run", is_flag=True, help="Preview files, updates, links, and conflicts without writing")
@click.option("--provider-url", default=None, help="OpenAI-compatible curation endpoint")
@click.option("--model", default=None, help="Curation model name")
def curate(raw_dir, raw_file, vault, dry_run, provider_url, model):
    """Curate raw query captures into idempotent concept/entity wiki pages."""
    from sift.curation import CurationError, EndpointSynthesizer, apply_curation, plan_curation

    vault_path = vault or Path.home() / "llm-wiki"
    if raw_dir is not None and raw_file is not None:
        raise click.UsageError("--file and --raw-dir are mutually exclusive")
    if raw_file is not None and raw_file.suffix.lower() != ".md":
        raise click.BadParameter("must be a Markdown file", param_hint="--file")
    raw_path = raw_dir or vault_path / "raw" / "queries"
    raw_path = raw_file or raw_path
    if raw_dir is None and raw_file is None and not raw_path.exists():
        legacy_raw = vault_path / "80-raw" / "82-queries"
        if legacy_raw.exists():
            raw_path = legacy_raw
    try:
        plans = plan_curation(raw_path, vault_path,
                              EndpointSynthesizer(url=provider_url, model=model))
        result = apply_curation(plans, vault_path, dry_run=dry_run)
    except CurationError as exc:
        raise click.ClickException(str(exc)) from exc
    mode = "Preview" if dry_run else "Curated"
    click.echo(f"{mode} {len(plans)} capture(s)")
    for key in ("created", "updated", "unchanged", "files", "links", "conflicts"):
        values = result[key]
        click.echo(f"  {key}: {len(values)}")
        for value in values:
            click.echo(f"    - {value}")


def _show_raw_results(items, *, header=None, text_key="excerpt"):
    """Display raw search/index results."""
    if header:
        click.secho(header, dim=True)
    for r in items:
        title = r.get("title") or "(no title)"
        url = r.get("url") or ""
        text = r.get(text_key) or ""
        click.echo(click.style(title, bold=True))
        if url:
            click.echo(click.style(url, fg="blue"))
        else:
            click.echo()
        if text:
            display = text[:200] if text_key == "body" else text
            click.echo(f"  {display}")
        click.echo()


@main.command()
@click.argument("query")
@click.option("--limit", default=10, type=click.IntRange(min=1), help="Max search results")
@click.option(
    "--no-llm",
    is_flag=True,
    help="Skip LLM synthesis — show raw results only",
)
@click.option("--live", is_flag=True, help="Answer from search snippets (faster, no page storage)")
@click.option("--wiki", "-w", is_flag=True, help="Save answer to ~/llm-wiki/raw/queries/")
@click.option("--wiki-slug", default=None, type=str,
              help="Filename slug for wiki output (default: auto from query)")
@click.pass_context
def ask(ctx, query, limit, no_llm, live, wiki, wiki_slug):
    """Ask a question — search index, pulse if empty, synthesize answer with citations."""
    from sift.pulse import PulseEngine
    from sift.synthesize import synthesize_stream, build_context

    db = ctx.obj["db"]

    # First pass: search existing index
    results = db.search(query, limit=limit)

    # --live flag: always search live, skip index
    if live:
        if not results:
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

        if no_llm:
            _show_raw_results(
                snippet_results,
                header="\n[Live search results — --no-llm]\n",
                text_key="body",
            )
            return

        context, source_text = build_context_from_snippets(snippet_results)

        click.secho("˶ᵔ ᵕ ᵔ˶ Thinking...\n", dim=True)

        collected = []
        for token in synthesize_stream(query, context):
            collected.append(token)

        final_answer = "".join(collected).strip()

        if final_answer and final_answer.startswith("[Synthesis error]"):
            click.secho(final_answer, fg="red")
            click.echo()
            _show_raw_results(
                snippet_results[:3],
                header="Showing raw search results instead:\n",
                text_key="body",
            )
            return

        # Print the answer
        click.echo("\n" + final_answer)

        # Print sources
        click.secho("\nSources:", bold=True)
        click.echo(source_text)

        # --wiki: save to raw/queries/
        if wiki:
            from sift.wiki import write_raw_source, slugify
            slug = wiki_slug or slugify(query)
            title = wiki_slug.replace("-", " ").title() if wiki_slug else query[:60]
            # Extract source URLs from the source list
            src_urls = []
            for line in source_text.split("\n"):
                if "http" in line:
                    for part in line.split():
                        if part.startswith("http"):
                            src_urls.append(part.rstrip(","))
            path = write_raw_source(title, slug, query, final_answer, src_urls)
            click.secho(f"\n[Wiki: saved to {path}]", dim=True)
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
        _show_raw_results(
            results[:limit],
            header="\n[Raw search results — --no-llm]\n",
        )
        return

    # LLM synthesis mode
    context, source_text = build_context(results, limit=limit)

    click.secho("˶ᵔ ᵕ ᵔ˶ Thinking...\n", dim=True)

    collected = []
    for token in synthesize_stream(query, context):
        collected.append(token)

    final_answer = "".join(collected).strip()

    if final_answer and final_answer.startswith("[Synthesis error]"):
        click.secho(final_answer, fg="red")
        click.echo()
        _show_raw_results(
            results[:3],
            header="Showing raw search results instead:\n",
        )
        return

    # Print the answer
    click.echo(final_answer)

    # Print sources
    click.secho("\nSources:", bold=True)
    click.echo(source_text)

    # --wiki: save to raw/queries/
    if wiki:
        from sift.wiki import (
            write_raw_source, slugify, split_answer_reasoning,
            extract_sources_from_answer
        )
        slug = wiki_slug or slugify(query)
        title = wiki_slug.replace("-", " ").title() if wiki_slug else query[:60]

        # Split to get reasoning (contains source references) and extract URLs
        answer, reasoning = split_answer_reasoning(final_answer)
        all_text = answer + "\n" + reasoning

        src_urls = extract_sources_from_answer(all_text)
        # Also extract from source_text (the bibliography we built)
        for line in source_text.split("\n"):
            if "http" in line:
                for part in line.split():
                    if part.startswith("http"):
                        url = part.rstrip(",)")
                        if url not in src_urls:
                            src_urls.append(url)

        path = write_raw_source(title, slug, query, final_answer, src_urls)
        click.secho(f"\n[Wiki: saved to {path}]", dim=True)


@main.command()
@click.argument("url")
@click.option("--max-pages", default=200, type=int, help="Max pages to crawl")
@click.pass_context
def crawl(ctx, url, max_pages):
    """Crawl a domain — discover pages via sitemap + internal links."""
    from sift.crawler import DomainCrawler

    db = ctx.obj["db"]
    crawler_obj = DomainCrawler(db)

    click.echo(f"Spider crawling {url}...")
    click.echo(f"   Max pages: {max_pages}")

    stats = crawler_obj.run(url, max_pages=max_pages)

    click.echo(f"\nCrawl complete")
    click.echo(f"   URLs discovered: {stats['urls_discovered']}")
    click.echo(f"   Pages stored:    {stats['pages_fetched']}")
    click.echo(f"   Errors:          {stats['errors']}")
    click.echo(f"   Robots skipped:  {stats.get('robots_skipped', 0)}")


if __name__ == "__main__":
    main()  # pylint: disable=no-value-for-parameter
