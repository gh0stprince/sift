<p align="center">
  <img src="assets/banner.png" alt="Sift banner" width="800">
</p>

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
Custom path: `sift --db /path/to/db.db <command>`

Data directories are automatically gitignored (see `.gitignore`).

### Optional encrypted storage

Encrypted storage uses SQLCipher and is opt-in; normal Sift databases remain
standard SQLite. Install the extra and provide the key out-of-band:

```bash
pip install -e '.[encrypted]'
export SIFT_DB_KEY='a-long-random-passphrase'
sift --encrypted --db ~/.sift/private.db search "transformer architecture"
```

Sift never writes the key to the database, source files, `.env` files, logs,
exceptions, or command output. `--encrypted` fails closed when `SIFT_DB_KEY`
is missing or incorrect and never falls back to plaintext. SQLCipher databases
use DELETE journaling and in-memory temporary storage to avoid unencrypted WAL,
SHM, and temp sidecar files. Close Sift cleanly before copying a backup; keep
the key separate from backups because losing it makes the data unrecoverable.

To migrate an existing plaintext database, use the explicit API (which leaves
the source untouched): `DB.migrate_plaintext(source, destination, key)` from
Python. Verify the encrypted destination opens with `SIFT_DB_KEY` before
removing the original. Migration does not securely erase the plaintext source.

The threat model covers an offline attacker who obtains the database file; it
does not protect data while the process is running, against a compromised host,
or against an attacker who obtains both the database and its key. SQLCipher is
an optional native dependency, so encrypted mode requires a compatible wheel
or local build for the target Python and platform.

## Development

Run tests:
```bash
pytest
```
