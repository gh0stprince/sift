# Configuration

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `OPENAI_API_KEY` | API key for LLM synthesis | None (required) |
| `OPENAI_BASE_URL` | OpenAI-compatible endpoint | `https://opencode.ai/zen/go/v1/chat/completions` |
| `OPENAI_MODEL` | Model to use for synthesis | `qwen3.7-plus` |
| `OPENCODE_GO_API_KEY` | Fallback API key | None |
| `AUXILIARY_APPROVAL_API_KEY` | Legacy fallback | None |
| `AUXILIARY_APPROVAL_MODEL` | Legacy fallback | None |

## `.env` File

Place a `.env` in the directory where you run `sift`. Example:

```bash
OPENAI_API_KEY=sk-your-api-key-here
OPENAI_BASE_URL=https://opencode.ai/zen/go/v1/chat/completions
OPENAI_MODEL=qwen3.7-plus
```

## CLI Overrides

Some values can be passed per-command. Check `sift ask --help` for options.
