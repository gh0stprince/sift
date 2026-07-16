# Frequently Asked Questions

## Setup & Install

### What Python version do I need?

**Python 3.10 or higher.** Sift uses `str | None` union syntax introduced in 3.10.
The CI runs on 3.10, 3.11, and 3.12.

### How do I install from source?

```bash
git clone https://github.com/gh0stprince/sift.git
cd sift
pip install -e .
```

### What dependencies are required?

See `requirements.txt`. Key ones:
- `ddgs` — web search
- `trafilatura` — page content extraction
- `httpx` — HTTP client for API calls
- `click` — CLI framework
- `python-dotenv` — `.env` file loading

---

## Configuration

### How do I set my API key?

Create a `.env` file in the project root:

```bash
OPENAI_API_KEY=sk-your-key-here
OPENAI_BASE_URL=https://opencode.ai/zen/go/v1/chat/completions
OPENAI_MODEL=qwen3.7-plus
```

Or set environment variables directly:

```bash
export OPENAI_API_KEY=sk-your-key-here
```

### What LLM providers work with Sift?

Any OpenAI-compatible API endpoint:
- OpenCode Go
- Together AI
- OpenRouter
- Local llama.cpp servers
- OpenAI itself

### How do I change the default model?

Set the `OPENAI_MODEL` environment variable or add it to `.env`:

```bash
OPENAI_MODEL=qwen3.7-plus
```

The fallback default is `qwen3.7-plus`.

### Why does synthesis say "No API key configured"?

Sift checks `OPENAI_API_KEY`, then `OPENCODE_GO_API_KEY`, then
`AUXILIARY_APPROVAL_API_KEY`. If none are set, synthesis fails with that message.
Make sure your `.env` file is in the working directory where you run `sift`.

---

## Usage

### What's the difference between `sift ask` and `sift ask --live`?

- `sift ask` — searches your local index first, pulses if empty, then synthesizes
- `sift ask --live` — skips index storage, answers directly from DDG snippets (faster)

### What is `sift pulse` and when should I use it?

`pulse` runs a web search, fetches the top pages, and stores them in the local
SQLite index. Use it to build a corpus on a topic before asking questions:

```bash
sift pulse "folk magic" --max-pages 50
sift ask "what is folk magic" --wiki --wiki-slug folk-magic
```

### How do I save answers to my wiki?

Add the `--wiki` or `-w` flag:

```bash
sift ask "what is folk magic" --wiki
```

Files are written to `~/llm-wiki/raw/queries/{slug}.md`.

### Where are wiki files stored?

`~/llm-wiki/raw/queries/` (your home directory).

### How do I search my indexed pages?

```bash
sift search "folk magic"
```

This runs an FTS5 full-text search against the local database.

---

## Database & Storage

### Where is the database stored?

By default: `~/.sift/sift.db`. Override with `--db`:

```bash
sift --db ./my-db.sqlite search "query"
```

### How do I clear or reset the database?

Delete the SQLite file:

```bash
rm ~/.sift/sift.db
```

### What gets cached and for how long?

Indexed pages store title, content, URL, fetch timestamp, and link depth. There is
no automatic TTL — freshness ranking deprioritizes older pages but does not delete
them.

---

## Development

### How do I run the test suite?

```bash
python -m pytest
```

### How do I run the linter?

```bash
python -m pylint $(git ls-files '*.py')
```

### Why does pylint complain about `import-outside-toplevel`?

The CLI uses lazy imports inside command functions to keep startup fast. This is
intentional and safe — the imports happen before any real work.

### Can I contribute?

The license is non-commercial for third parties. The author (gh0stprince) retains
commercial rights. Open an issue or PR for bug fixes and improvements.

---

## Troubleshooting

### Why am I getting JSON decode errors?

Usually means the model returned non-JSON (HTML error page, rate limit response,
etc.). Check your `OPENAI_BASE_URL` and API key.

### DeepSeek models show reasoning text — how do I clean it?

Sift's `synthesize_stream` yields both `content` and `reasoning_content` tokens.
The `sift.wiki.clean_answer()` function strips thinking preamble and extracts the
final answer paragraph. Use `--wiki` to get cleaned output.

### Search results seem stale

Run `sift search "your query" --fresh` to boost relevant pages fetched more
recently. Normal search orders by FTS5 relevance only. `--fresh` changes ranking;
it does not fetch the web or update stored pages. Use `sift pulse`, `sift crawl`,
or `sift ingest` to refresh the index first.
