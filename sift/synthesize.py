"""Answer synthesis — LLM with inline citations from search results."""

import json
import os
from typing import Any, Generator

from dotenv import load_dotenv
import httpx

load_dotenv()

# Default endpoint — OpenCode Go (this session's active provider)
# Used when no API key or custom endpoint is configured.
DEFAULT_API_URL = (
    os.environ.get("OPENAI_BASE_URL")
    or "https://opencode.ai/zen/go/v1/chat/completions"
)

# API key resolution order: explicit arg > env var > None
# For local dev, set OPENAI_API_KEY, OPENCODE_GO_API_KEY or AUXILIARY_APPROVAL_API_KEY
DEFAULT_API_KEY = (
    os.environ.get("OPENAI_API_KEY")
    or os.environ.get("OPENCODE_GO_API_KEY")
    or os.environ.get("AUXILIARY_APPROVAL_API_KEY")
)

# Model: use the active model from provider config
DEFAULT_MODEL = (
    os.environ.get("OPENAI_MODEL")
    or os.environ.get("AUXILIARY_APPROVAL_MODEL")
    or "qwen3.7-plus"
)


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

    .. deprecated::
       Use :func:`synthesize_stream` instead. This non-streaming variant
       is kept only as a fallback for direct module-level usage.

    Returns the assistant's response as a string, or an error message
    prefixed with ``[Synthesis error]`` if the call fails.
    """
    if not api_key:
        return (
            "[Synthesis error] No API key configured for LLM synthesis.\n"
            "Set OPENAI_API_KEY or configure auth.json."
        )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    system_prompt = (
        "You are a research assistant. Answer the user's question based ONLY "
        "on the provided context. Cite sources inline using [1], [2], etc. "
        "If the context doesn't contain enough information, say so clearly. "
        "Be comprehensive — aim for 3-5 paragraphs. Always end with a clear summary."
        " Do NOT include any thinking, reasoning, analysis, or internal monologue."
        " Just provide the answer directly."
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
        raw = content or reasoning or ""
        if not raw:
            return "[Synthesis error] Empty response from model."
        return raw.strip()
    except httpx.HTTPStatusError as e:
        body = e.response.text[:500]
        return f"[Synthesis error] HTTP {e.response.status_code}: {body}"
    except httpx.RequestError as e:
        return f"[Synthesis error] Request failed: {e}"
    except (KeyError, IndexError, json.JSONDecodeError) as e:
        return f"[Synthesis error] Unexpected response format: {e}"


def synthesize_stream(
    query: str,
    context: str,
    api_url: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> Generator[str, None, None]:
    """Stream synthesis tokens as they arrive via SSE.

    Note: DeepSeek models emit all content as ``reasoning_content``
    with empty ``content`` fields. We yield everything as-is and let
    the caller clean the answer with ``sift.wiki.clean_answer()``.

    Falls back to yielding the single full response if streaming is
    not supported.
    """
    url = api_url or DEFAULT_API_URL
    mdl = model or DEFAULT_MODEL
    key = api_key or DEFAULT_API_KEY

    if not key:
        yield "[Synthesis error] No API key configured."
        return

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    system_prompt = (
        "You are a research assistant. Answer the user's question based ONLY "
        "on the provided context. Cite sources inline using [1], [2], etc. "
        "If the context doesn't contain enough information, say so clearly. "
        "Be comprehensive — aim for 3-5 paragraphs with clear structure. "
        "Always end with a clear summary. "
        "Do NOT include any thinking, reasoning, analysis, or internal monologue. "
        "Just provide the answer directly."
    )

    payload = {
        "model": mdl,
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
        "stream": True,
    }

    try:
        with httpx.stream(
            "POST", url, json=payload, headers=headers, timeout=120
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    data_str = line[6:]
                    if data_str.strip() == "[DONE]":
                        break
                    try:
                        data = json.loads(data_str)
                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        token = delta.get("content", "") or delta.get(
                            "reasoning_content", ""
                        )
                        if token:
                            yield token
                    except json.JSONDecodeError:
                        continue
    except httpx.HTTPStatusError as e:
        yield f"\n[Synthesis error] HTTP {e.response.status_code}"
    except httpx.RequestError as e:
        yield f"\n[Synthesis error] Request failed: {e}"
