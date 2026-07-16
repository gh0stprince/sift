# Usage

## Commands

### `sift ask`

Ask a question. Searches index, pulses if empty, synthesizes answer with citations.

```bash
sift ask "what is folk magic"
sift ask "what is folk magic" --wiki --wiki-slug folk-magic
sift ask "latest ai news" --live
```

Flags:
- `--live` — answer from DDG snippets, skip storage
- `--wiki` / `-w` — save answer to `~/llm-wiki/raw/queries/`
- `--wiki-slug` — custom filename slug
- `--no-llm` — show raw search results only
- `--limit` — max sources to include (default 10)

### `sift pulse`

Search and fetch pages into the local index.

```bash
sift pulse folk-magic --max-pages 50
```

### `sift search`

Full-text search the local index.

```bash
sift search "folk magic"
```

### `sift feeds` and `sift ingest`

Register RSS/Atom feeds, then fetch and index their entries.

```bash
sift feeds init
sift feeds list
sift feeds add "My Blog" "https://example.com/feed.xml"
sift ingest --max-per-feed 20
```

### Freshness-ranked search

`search` normally orders results by FTS5 relevance. Add `--fresh` to boost
relevant pages fetched more recently; the flag does not fetch the web or refresh
stored pages.

```bash
sift search "folk magic" --fresh
```

### `sift crawl`

Crawl a domain and store pages. Sift fetches and honors each site's
robots.txt using the configured Sift User-Agent. If robots.txt is missing,
unreachable, or malformed, Sift skips that origin (fail-closed) rather than
silently bypassing its exclusions.

```bash
sift crawl https://example.com --max-pages 100
```
