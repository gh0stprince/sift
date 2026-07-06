# Troubleshooting

## "No API key configured for LLM synthesis"

**Cause:** `OPENAI_API_KEY` (or fallback) not set.

**Fix:**

```bash
# Check
env | grep OPENAI_API_KEY

# Fix — create .env in your working directory
echo "OPENAI_API_KEY=sk-..." > .env
```

## `NameError: name 'json' is not defined`

**Cause:** A bug in older versions where `json` was not imported in `synthesize.py`.

**Fix:** Update to the latest commit on `main`.

## Model returns reasoning text instead of answer

**Cause:** DeepSeek and some models emit thinking tokens in `reasoning_content`.

**Fix:** Use `--wiki` which runs `clean_answer()`, or update to a version that
handles both `content` and `reasoning_content` fields.

## `ModuleNotFoundError: No module named 'trafilatura'`

**Cause:** Dependencies not installed.

**Fix:**

```bash
pip install -e .
```

## pylint CI fails on Python 3.8 or 3.9

**Cause:** `str | None` syntax requires 3.10+.

**Fix:** The CI matrix was updated to 3.10/3.11/3.12. Update your branch.

## Database is locked

**Cause:** Another sift process is using the database.

**Fix:** Close other sift instances, or use a different `--db` path.
