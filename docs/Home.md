# Sift Wiki

Welcome to the Sift wiki — AI-powered web research from your terminal.

## Quick Links

- [FAQ](FAQ) — Frequently asked questions
- [Setup](Setup) — Installation and first run
- [Configuration](Configuration) — API keys, models, and env vars
- [Usage](Usage) — Commands and flags
- [Troubleshooting](Troubleshooting) — Common errors and fixes

## What is Sift?

Sift is a CLI tool for web research powered by LLMs. It indexes web pages, searches
DuckDuckGo, fetches RSS feeds, and synthesizes cited answers using any
OpenAI-compatible API endpoint.

## Quick Start

```bash
pip install -e .
echo "OPENAI_API_KEY=sk-..." > .env
echo "OPENAI_BASE_URL=https://opencode.ai/zen/go/v1/chat/completions" >> .env
sift ask "what is folk magic"
```
