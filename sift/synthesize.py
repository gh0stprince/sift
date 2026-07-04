"""Answer synthesis — LLM with inline citations from search results."""

import json
import os
from typing import Any

import httpx

# Default endpoint — OpenCode Go (this session's active provider)
# Used when no API key or custom endpoint is configured.
DEFAULT_API_URL = "https://opencode.ai/zen/go/v1/chat/completions"

# API key resolution order: explicit arg > env var > auth.json lookup
DEFAULT_API_KEY = None
_auth_path = os.path.expanduser("~/AppData/Local/hermes/auth.json")
if os.path.exists(_auth_path):
    try:
        with open(_auth_path) as f:
            auth = json.load(f)
        pool = auth.get("credential_pool", {}).get("opencode-go", [])
        for cred in pool:
            if cred.get("auth_type") == "api_key" and cred.get("access_token"):
                DEFAULT_API_KEY = cred["access_token"]
                break
    except Exception:
        pass


# Model: use the active model from provider config
DEFAULT_MODEL = os.environ.get("AUXILIARY_APPROVAL_MODEL") or "deepseek-v4-flash"


def build_context(results: list[dict[str, Any]], limit: int = 10) -> tuple[str, str]:
    """Build context block and source-bibliography string from search results.

    Returns (context_md, sources_text) where:
    - context_md is a markdown section with numbered sources and their excerpts
    - sources_text is a compact [1] title — URL per line for display
    """
    parts = []
    sources = []
    for i, r in enumerate(results[:limit], 1):
        title = r.get("title") or "(no title)"
        url = r.get("url") or ""
        content = r.get("content") or r.get("excerpt") or ""
        # Use up to 2000 chars per source
        body = content[:2000]
        parts.append(f"[{i}] {title}\nURL: {url}\n\n{body}")
        sources.append(f"  [{i}] {title}")
        sources.append(f"       {url}")

    context = "\n\n---\n\n".join(parts)
    source_text = "\n".join(sources)
    return context, source_text


def build_context_from_snippets(results: list[dict[str, str]], limit: int = 10) -> tuple[str, str]:
    """Build context from DDG search result snippets (no full page content).

    results is a list of {"url", "title", "body"} dicts as returned
    by PulseEngine._search_ddg(). Produces shorter context than
    build_context() because there's no extracted page text.
    """
    parts = []
    sources = []
    for i, r in enumerate(results[:limit], 1):
        title = r.get("title") or "(no title)"
        url = r.get("url") or ""
        body = r.get("body") or ""
        parts.append(f"[{i}] {title}\nURL: {url}\n\n{body}")
        sources.append(f"  [{i}] {title}")
        sources.append(f"       {url}")

    context = "\n\n---\n\n".join(parts)
    source_text = "\n".join(sources)
    return context, source_text


def synthesize(
    query: str,
    context: str,
    api_url: str = DEFAULT_API_URL,
    model: str = DEFAULT_MODEL,
    api_key: str | None = DEFAULT_API_KEY,
) -> str:
    """Send query + context to an OpenAI-compatible chat endpoint.

    Returns the assistant's response as a string, or an error message
    prefixed with ``[Synthesis error]`` if the call fails.
    """
    if not api_key:
        return "[Synthesis error] No API key configured for LLM synthesis.\nSet OPENCODE_GO_API_KEY or configure auth.json."

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    system_prompt = (
        "You are a research assistant. Answer the user's question based ONLY "
        "on the provided context. Cite sources inline using [1], [2], etc. "
        "If the context doesn't contain enough information, say so clearly. "
        "Be concise — aim for 2-4 paragraphs. Always end with a clear summary."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": (
                    f"Context:\n{context}\n\n"
                    f"Question: {query}\n\n"
                    f"Answer based only on the context above. "
                    f"Cite sources inline with [1], [2], etc."
                ),
            },
        ],
        "temperature": 0.3,
        "max_tokens": 2048,
    }

    try:
        resp = httpx.post(api_url, json=payload, headers=headers, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        # Extract content, skipping reasoning tokens
        choice = data["choices"][0]["message"]
        content = choice.get("content", "")
        reasoning = choice.get("reasoning_content", "")
        if not content and reasoning:
            # Some models only return reasoning_content when they cut off;
            # fall back to reasoning as the visible output
            content = reasoning
        if not content:
            return "[Synthesis error] Empty response from model."
        return content.strip()
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500]
        return f"[Synthesis error] HTTP {e.response.status_code}: {body}"
    except httpx.RequestError as e:
        return f"[Synthesis error] Request failed: {e}"
    except (KeyError, json.JSONDecodeError) as e:
        return f"[Synthesis error] Unexpected response format: {e}"
