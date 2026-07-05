# Sift

AI-powered web research tool. Sift searches the web, extracts content from
pages, and synthesizes findings — giving you concise answers grounded in
real sources.

## Features

- **Search** — Full-text search over indexed pages with recency boosting
- **Pulse** — Recursive research: discover content by following links from search results
- **Feeds** — Ingest and index RSS/Atom feeds (Lobsters, Hacker News, ArXiv, etc.)
- **Ask** — Get AI-synthesized answers with inline citations from search results
- **Wiki** — Save research outputs directly to your LLM wiki (planned, currently giving garbage output)

## Installation

```bash
pip install -e .
```

## Configuration

Sift requires an API key for answer synthesis. Set one of:

```bash
export OPENCODE_GO_API_KEY="your-api-key"
# or
export AUXILIARY_APPROVAL_API_KEY="your-api-key"
```

Optional environment variables:
- `AUXILIARY_APPROVAL_MODEL` — Model to use (default: `deepseek-v4-flash`)

## Usage

### Search indexed content

```bash
sift search "transformer architecture"
sift search "latest AI papers" --fresh  # boost recent results
```

### Run a research pulse

Recursively discover content from a query:

```bash
sift pulse "attention mechanism" --depth 2 --max-pages 50
```

### Manage feeds

```bash
sift feeds init                    # add default feeds
sift feeds list                    # show registered feeds
sift feeds add "My Blog" "https://example.com/feed.xml"
sift ingest --max-per-feed 10      # fetch and index feed entries
```

Default feeds include: Lobsters, Hacker News, ArXiv (CS.AI, CS.LG, q-bio.NC), LessWrong, Astral Codex Ten.

### Ask with AI synthesis

```bash
sift ask "What is the transformer architecture?"
sift ask "Explain RLHF" --limit 5          # use top 5 sources
sift ask "Latest LLM benchmarks" --wiki llm-benchmarks-2024
```

The `--wiki` flag saves the raw results to `~/llm-wiki/raw/queries/<slug>.md`.

### View statistics

```bash
sift stats
```

## Data Storage

Sift stores data in a local SQLite database:
- Default location: `~/.sift/sift.db`
- Custom path: `sift --db /path/to/db.db <command>`

Data directories are automatically gitignored (see `.gitignore`).

## Development

Run tests:
```bash
pytest
```
