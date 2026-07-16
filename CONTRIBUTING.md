# Contributing to Sift

Thanks for wanting to contribute to Sift. This is a small, personal project that
I build for my own research workflow, but I'm open to contributions that make it
better for everyone who uses it.

This document covers how to set up a dev environment, what standards to follow,
and what to expect when you open a pull request.

---

## Table of Contents

- [License Note](#license-note)
- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Project Structure](#project-structure)
- [Development Workflow](#development-workflow)
- [Coding Standards](#coding-standards)
- [Testing](#testing)
- [Documentation](#documentation)
- [Pull Request Process](#pull-request-process)
- [Reporting Bugs](#reporting-bugs)
- [Feature Requests](#feature-requests)

---

## License Note

Sift uses a **custom non-commercial license** (see `LICENSE`). By contributing,
you agree that your contributions will be licensed under the same terms. The
copyright holder (gh0stprince) retains exclusive commercial rights.

If you're contributing code you didn't write yourself (e.g., a snippet from
another project), call it out in the PR description so we can verify license
compatibility.

---

## Code of Conduct

Be respectful. This is a small project — disagreements happen, but keep them
constructive. Don't be a jerk. If someone is being a jerk, flag it in an issue
or email the maintainer directly.

---

## Getting Started

### Prerequisites

- **Python 3.10+** — Sift uses `str | None` union syntax and other 3.10+ features.
- **git**
- **pip**

### Setup

```bash
# Clone the repo
git clone https://github.com/gh0stprince/sift.git
cd sift

# Create a virtual environment (recommended)
python3 -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows

# Install in editable mode with dev dependencies
pip install -e .
pip install pytest
```

### Verify

```bash
# Check the CLI works
sift --help

# Run the test suite
pytest
```

### API Key (for synthesis features)

Create a `.env` file in the project root:

```bash
OPENAI_API_KEY="your-key"
OPENAI_BASE_URL="https://opencode.ai/zen/go/v1/chat/completions"
OPENAI_MODEL="qwen3.7-plus"
```

Sift checks these env vars in order: `OPENAI_API_KEY` →
`OPENCODE_GO_API_KEY` → `AUXILIARY_APPROVAL_API_KEY`. The
base URL defaults to OpenCode Go if unset.

---

## Project Structure

```
sift/
├── sift/                  # Main package
│   ├── __init__.py
│   ├── __main__.py
│   ├── cli.py             # Click CLI entry point and commands
│   ├── crawler.py         # Page fetching and content extraction
│   ├── db.py              # SQLite database layer
│   ├── feeds.py           # RSS/Atom feed ingestion
│   ├── pulse.py           # Recursive research pulse
│   ├── synthesize.py      # LLM answer synthesis with citations
│   └── wiki.py            # LLM wiki exports
├── tests/                 # Test suite (mirrors sift/ structure)
│   ├── test_crawler.py
│   ├── test_db.py
│   ├── test_feeds.py
│   ├── test_pulse.py
│   └── test_synthesize.py
├── docs/                  # User-facing documentation
│   ├── Configuration.md
│   ├── FAQ.md
│   ├── Setup.md
│   ├── Troubleshooting.md
│   └── Usage.md
├── .github/workflows/     # CI configuration
├── setup.py               # Package definition
├── requirements.txt       # Dependencies
├── LICENSE                # Non-commercial license
└── README.md
```

---

## Development Workflow

### Branching

- **`main`** is the stable branch. PRs should target `main`.
- Feature branches: `feat/short-description`
- Bug fixes: `fix/short-description`
- Docs/CI chores: `chore/short-description`

### Commits

Keep commits focused. A commit should do one thing. Write descriptive commit
messages:

```
feat: add --fresh flag to search command for recency boosting

When the --fresh flag is passed, search results are boosted by their
crawl timestamp so newer content appears higher in results.

Closes #42
```

Prefixes: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.

Avoid vague commit titles like `improvements` or `updates`. Use scoped,
descriptive messages so review history and release notes stay precise.

### Before You Push

1. Run `make prepush` — this runs lint, unit tests, and a CLI smoke check.
2. If your change touches integration behavior, also run `pytest -m integration`.
3. Address new warnings/failures before opening or updating your PR.

---

## Coding Standards

### Python

- **Python 3.10+** — use modern syntax (`str | None` over `Optional[str]`).
- **Type hints** on all function signatures. Use `from __future__ import annotations`
  if you need forward references (the project already does this in `cli.py`).
- **f-strings** over `.format()` or `%` formatting.
- **Docstrings** for public functions and methods (PEP 257 style — triple-quoted,
  summary line + body when needed).

### Imports

Sorted: standard library → third-party → local. Use blank lines between groups.

```python
from __future__ import annotations

from pathlib import Path

import click
from trafilatura import extract

from sift.db import DB
```

### Linting

The CI runs `pylint` on every push. You can run it locally:

```bash
pylint sift/ tests/
```

If a rule produces a false positive, add a `# pylint: disable=...` comment with
a brief reason. Don't disable rules globally without discussing in the PR.

---

## Testing

### Running Tests

```bash
pytest                          # all tests
pytest -v                       # verbose
pytest tests/test_db.py         # single file
pytest -k "feed"                # tests matching keyword
```

### Writing Tests

- Each module in `sift/` should have a corresponding test file in `tests/`.
- Use pytest's `tmp_path` fixture for filesystem tests.
- Mock network calls (`httpx`, `ddgs`) so tests are fast and
  don't depend on external services.
- Name test functions `test_<thing_being_tested>`.

```python
def test_search_fresh_flag_boosts_recent_results():
    """--fresh should order newer crawl dates above older ones."""
    ...
```

### Test Coverage

There's no hard coverage threshold, but new features should come with tests
for the happy path and at least one edge case. If it's hard to test (network
heavy, complex LLM interaction), document what you tested manually.

---

## Documentation

- If you add a CLI flag or command, update the relevant docstring (used by
  `--help`) and the `docs/Usage.md` page.
- If you change the config or environment variables, update
  `docs/Configuration.md` and `docs/Setup.md`.
- Docstrings use PEP 257 style. No need for Sphinx/Google format — plain
  readable docstrings are fine for this project.

---

## Pull Request Process

1. **Open early** — even a draft PR is useful for discussing approach before
   writing a lot of code.
2. **Describe what and why** — what problem does this solve? How did you test it?
3. **Keep it focused** — one feature or fix per PR. Large PRs are hard to review
   and more likely to be deferred.
4. **CI must pass** — the pylint check and tests need to be green.
5. **Rebase on main** before final review to keep history clean.
6. **Review can be informal** — I may ask questions, suggest simplifications, or
   request tests. This is a small project so turnaround is usually quick.
7. **Merging** — I'll squash-merge into `main` unless there's a reason to keep
   history.

### Branch Protection (Repository Setting)

Protect `main` in GitHub with these required checks:

- `Pylint`
- `Tests`
- `CodeQL`

Also enable:

- Require branches to be up to date before merging.

### PR Template

The `.github/PULL_REQUEST_TEMPLATE.md` covers the basics. Fill it in.

---

## Reporting Bugs

Open a [GitHub Issue](https://github.com/gh0stprince/sift/issues/new) and
include:

- **What you ran** — the exact command and flags
- **What happened** — error output, traceback, unexpected behavior
- **What you expected**
- **Your environment** — Python version, OS, Sift version (git commit hash)
- **Steps to reproduce** — ideally minimal

Bug reports are valuable even without a fix attached.

---

## Feature Requests

Open a [GitHub Issue](https://github.com/gh0stprince/sift/issues/new) and
describe:

- **What you want to do** — the problem or gap you see
- **Why it fits Sift** — this is a personal research tool, not a general-purpose
  web scraper. Features that are clearly about personal research, local-first
  data ownership, or AI-assisted discovery are in scope.
- **How it might work** — rough sketch is fine, helps discussion

I can't promise every feature request will be accepted, but I'll explain why
if it's not a fit.
