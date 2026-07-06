# Setup Guide

## Prerequisites

- Python 3.10+
- pip

## Install

```bash
git clone https://github.com/gh0stprince/sift.git
cd sift
pip install -e .
```

## Verify

```bash
sift --help
python -m pytest
```

## Configure API Access

Create `.env`:

```bash
OPENAI_API_KEY=sk-your-key
OPENAI_BASE_URL=https://opencode.ai/zen/go/v1/chat/completions
OPENAI_MODEL=qwen3.7-plus
```

Run a test query:

```bash
sift ask "hello world"
```
