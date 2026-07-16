# Copilot Instructions for Sift

## Project overview
- Sift is a Python 3.10+ CLI for local-first web research (search, pulse crawling, feeds ingest, answer synthesis, wiki curation).
- Core package code lives in `/home/runner/work/sift/sift/sift/`.
- Tests mirror modules in `/home/runner/work/sift/sift/tests/`.

## Development setup and validation
- Install in editable mode: `pip install -e .`
- Preferred local gate: `make prepush` (lint + unit tests + CLI smoke check).
- Run unit tests directly: `pytest -m "not integration"`
- Run integration tests when relevant: `pytest -m integration`
- Run linting directly: `pylint sift/ tests/`
- Keep changes focused and verify affected CLI behavior with `sift --help` and relevant commands.

## Coding conventions
- Use Python 3.10+ style (`str | None` unions, modern typing).
- Add type hints on function signatures.
- Use f-strings for string formatting.
- Prefer docstrings on public functions and methods (PEP 257 style).
- Keep imports grouped: standard library, third-party, then local.

## Repository-specific expectations
- Avoid broad refactors in feature/fix PRs; keep commits focused.
- Keep PR descriptions aligned with repository template sections: `## Impact`, `## Risk`, and `## Validation`.
- Keep CI and workflow expectations aligned with `.github/workflows/`:
  - `pylint.yml` + `tests.yml` run on every push and PR (Python 3.10/3.11/3.12).
  - `nightly-checks.yml` runs integration tests nightly (`pytest -m integration -v`) and on manual dispatch.
  - `codeql.yml` is kept lightweight because GitHub CodeQL default setup handles scanning; it still runs on `main` push/PR and a weekly schedule.
  - `release.yml` builds and publishes GitHub releases for version tags matching `v*`.
  - `pr-automation.yml` enforces PR template sections and auto-labels PRs by changed paths.
- When opening/updating a PR, include all required template headers in order (`## Impact`, `## Risk`, `## Validation`) so automation checks pass.
- Update docs when user-facing CLI flags, commands, or config/environment variables change:
  - Usage docs: `/home/runner/work/sift/sift/docs/Usage.md`
  - Config/setup docs: `/home/runner/work/sift/sift/docs/Configuration.md`, `/home/runner/work/sift/sift/docs/Setup.md`
- For tests, mock network-dependent behavior to keep suites fast and deterministic.

## Safety and behavior
- Do not commit API keys, credentials, or `.env` secrets.
- Respect the project's local-first and privacy-oriented behavior (e.g., conservative robots handling, provenance-preserving wiki workflow).
