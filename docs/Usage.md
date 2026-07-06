# Usage

## Commands

### `sift ask`

Ask a question. Searches index, pulses if empty, synthesizes answer with citations.

```bash
sift ask "what is folk magic"
sift ask folk-magic "what is folk magic" --wiki
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

### `sift feed`

Fetch and index RSS feeds.

```bash
sift feed
sift feed --limit 20
```

### `sift crawl`

Crawl a domain and store pages.

```bash
sift crawl https://example.com --max-pages 100
```
