# Copilot Instructions for Sift

## Project overview
- Sift is a Python 3.10+ CLI for local-first web research (search, pulse crawling, feeds ingest, answer synthesis, wiki curation).
- Core package code lives in `/home/runner/work/sift/sift/sift/`.
- Tests mirror modules in `/home/runner/work/sift/sift/tests/`.

## Development setup and validation
- Install in editable mode: `pip install -e .`
- Run tests: `pytest`
- Run linting: `pylint sift/ tests/`
- Keep changes focused and verify affected CLI behavior with `sift --help` and relevant commands.

## Coding conventions
- Use Python 3.10+ style (`str | None` unions, modern typing).
- Add type hints on function signatures.
- Use f-strings for string formatting.
- Prefer docstrings on public functions and methods (PEP 257 style).
- Keep imports grouped: standard library, third-party, then local.

## Repository-specific expectations
- Avoid broad refactors in feature/fix PRs; keep commits focused.
- Update docs when user-facing CLI flags, commands, or config/environment variables change:
  - Usage docs: `/home/runner/work/sift/sift/docs/Usage.md`
  - Config/setup docs: `/home/runner/work/sift/sift/docs/Configuration.md`, `/home/runner/work/sift/sift/docs/Setup.md`
- For tests, mock network-dependent behavior to keep suites fast and deterministic.

## Safety and behavior
- Do not commit API keys, credentials, or `.env` secrets.
- Respect the project's local-first and privacy-oriented behavior (e.g., conservative robots handling, provenance-preserving wiki workflow).
