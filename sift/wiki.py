"""Wiki output helpers — write sift ask results to the LLM wiki."""

from __future__ import annotations

import json
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

import httpx

WIKI_RAW_DIR = Path.home() / "llm-wiki" / "raw" / "queries"

# API config for cleanup pass
CLEANUP_API_URL = (
    os.environ.get("OPENAI_BASE_URL")
    or "https://opencode.ai/zen/go/v1/chat/completions"
)
CLEANUP_API_KEY = (
    os.environ.get("OPENAI_API_KEY")
    or os.environ.get("OPENCODE_GO_API_KEY")
    or os.environ.get("AUXILIARY_APPROVAL_API_KEY")
)
CLEANUP_MODEL = (
    os.environ.get("OPENAI_MODEL")
    or os.environ.get("AUXILIARY_APPROVAL_MODEL")
    or "qwen3.7-plus"
)


def cleanup_with_llm(raw_synthesis: str, query: str) -> tuple[str, str]:
    """Use LLM to extract clean answer and summary from raw synthesis.

    Returns (clean_answer, one_line_summary).
    """
    if not CLEANUP_API_KEY:
        # Fallback: return raw synthesis as-is
        return raw_synthesis, ""

    system_prompt = (
        "You are a document cleaner. Your job is to extract ONLY the final "
        "answer from a research synthesis that may contain thinking steps.\n\n"
        "Rules:\n"
        "1. Remove ALL thinking, analysis, planning, and reasoning steps\n"
        "2. Remove numbered steps like '1. **Analyze**', '2. **Synthesize**'\n"
        "3. Keep only the actual answer paragraphs\n"
        "4. Preserve inline citations [1], [2], etc.\n"
        "5. Generate a one-line summary (max 100 chars) of the key finding\n\n"
        "Output format:\n"
        "SUMMARY: <one line summary>\n"
        "---\n"
        "<clean answer here>"
    )

    user_prompt = f"Query: {query}\n\nRaw synthesis:\n{raw_synthesis}"

    headers = {
        "Authorization": f"Bearer {CLEANUP_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": CLEANUP_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 2048,
    }

    try:
        resp = httpx.post(CLEANUP_API_URL, json=payload, headers=headers, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        content = data["choices"][0]["message"].get("content", "")

        # Parse SUMMARY: ... --- ... format
        if "SUMMARY:" in content and "---" in content:
            parts = content.split("---", 1)
            summary = parts[0].replace("SUMMARY:", "").strip()
            clean = parts[1].strip()
            return clean, summary

        # Fallback: return everything as answer
        return content, ""

    except Exception:
        # On any error, return raw synthesis
        return raw_synthesis, ""


def slugify(text: str, max_len: int = 60) -> str:
    """Convert text to a wiki-safe filename slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-")


def split_answer_reasoning(text: str) -> tuple[str, str]:
    """Split synthesis into (answer, reasoning) components.

    Models like DeepSeek emit their full reasoning process followed by
    the actual answer. The answer typically starts after numbered thinking
    steps ("1. **Identify...", "2. **Analyze...") end.

    Returns (answer, reasoning) tuple.
    """
    lines = text.split("\n")

    reasoning_lines = []
    answer_lines = []
    in_answer = False

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue

        # Detect if this line is part of numbered thinking process
        # Pattern: "1.  **Verb**" or "1. **Verb**" at start of line
        is_numbered_thinking = bool(re.match(r"^\d+\.\s+\*\*", stripped))

        # Or bullet points that are part of analysis (indented or with asterisk)
        is_analysis_bullet = bool(re.match(
            r"^(\s+\*|\*\s+)(Target|Goal|Constraints|Context|"
            r"Identify|Analyze|Extract|Structure|Refine|Drafting|"
            r"Self-Correction|Key points|Wait,|"
            r"Synthesize|Structure|Paragraph|Summary|Refine|Citation)",
            stripped, re.IGNORECASE
        ))

        # Or any line that's clearly indented continuation of analysis
        is_indented_continuation = bool(re.match(
            r"^\s+(Context|Wait,|It details|It contrasts|Mentions|"
            r"Example given|For example|Specifically|The)",
            stripped
        ))

        # Or standalone meta-thinking lines
        is_meta_line = bool(re.match(
            r"^(Thinking\.?|Let me|I should|From the context|The context also|"
            r"Key points about|I can |I will |I need to|From the|Based on|"
            r"The user is|The context discusses|This approach reflects|"
            r"Let's draft:)",
            stripped, re.IGNORECASE
        ))

        if in_answer:
            # Once we're in answer, everything goes to answer
            answer_lines.append(line)
        elif is_numbered_thinking or is_analysis_bullet or is_meta_line or is_indented_continuation:
            # Still in reasoning section
            reasoning_lines.append(line)
        else:
            # First non-reasoning line starts the answer
            in_answer = True
            answer_lines.append(line)

    reasoning = "\n".join(reasoning_lines).strip()
    answer = "\n".join(answer_lines).strip()

    # Fallback: if no clear split found, look for paragraph transition
    if not answer and reasoning:
        paragraphs = [p.strip() for p in reasoning.split("\n\n") if p.strip()]
        # Find first paragraph that doesn't start with meta patterns
        for j, para in enumerate(paragraphs):
            if not re.match(r"^(Thinking|Let me|I should|\d+\.\s+\*\*)", para, re.IGNORECASE):
                answer = "\n\n".join(paragraphs[j:])
                reasoning = "\n\n".join(paragraphs[:j])
                break

    # Final fallback: if still no answer, take last paragraph as answer
    if not answer:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if paragraphs:
            answer = paragraphs[-1]
            reasoning = "\n\n".join(paragraphs[:-1]) if len(paragraphs) > 1 else ""

    return answer, reasoning


def clean_answer(answer: str) -> str:
    """Extract just the answer portion, stripping any remaining meta language."""
    answer_part, _ = split_answer_reasoning(answer)
    return answer_part


def write_raw_source(
    title: str,
    slug: str,
    query: str,
    synthesis: str,
    sources: list[str],
) -> str:
    """Write synthesis + sources as an immutable raw-source file.

    Creates ``{WIKI_RAW_DIR}/{slug}.md`` with proper frontmatter.
    Runs LLM cleanup pass to extract clean answer from raw synthesis.
    If the file already exists, appends a new Update section.
    Returns the absolute path of the written file.
    """
    today = date.today().isoformat()
    now_iso = datetime.now(timezone.utc).isoformat()

    # Run LLM cleanup pass to get clean answer and summary
    clean_answer, summary = cleanup_with_llm(synthesis, query)

    # Also try local split as fallback content for reasoning section
    _, reasoning = split_answer_reasoning(synthesis)

    # Build body - use LLM-cleaned answer
    body = clean_answer

    # Build frontmatter
    tag_lines = "  - topic:research"

    # Add summary as description if we got one
    summary_line = f'description: "{summary[:100]}"\n' if summary else ""

    frontmatter = f"""---
title: "{title}"
created: {today}
updated: {today}
type: raw-source
{summary_line}tags:
{tag_lines}
source_query: "{query}"
ingested: {today}
"""

    if sources:
        source_lines = "\n".join(
            f'  - url: "{s}"\n    accessed: {today}' for s in sources[:10]
        )
        frontmatter += f"sources:\n{source_lines}\n"

    frontmatter += "---"

    # Add raw reasoning as appendix if present
    if reasoning:
        body += (
            f"\n\n<details>\n<summary>Raw reasoning (model thinking)</summary>\n\n"
            f"{reasoning}\n</details>"
        )

    if sources:
        body += "\n\n## Sources\n\n"
        for i, s in enumerate(sources, 1):
            body += f"{i}. {s}\n"

    body += f"\n\n---\n*Generated by sift ask --wiki on {now_iso}*"

    content = f"{frontmatter}\n\n{body}\n"

    # Write
    WIKI_RAW_DIR.mkdir(parents=True, exist_ok=True)
    page_path = WIKI_RAW_DIR / f"{slug}.md"

    # Append if exists (raw sources accumulate updates)
    if page_path.exists():
        existing = page_path.read_text(encoding="utf-8")
        fm_end = existing.find("---", 3)
        if fm_end > 0:
            existing_body = existing[fm_end + 3:].strip()
            body = existing_body + f"\n\n---\n\n## Update - {today}\n\n{body}"
            existing = (
                existing[: fm_end + 3]
                + "\nupdated: " + today + "\n"
                + existing[fm_end + 3:]
            )
            existing = existing[: existing.rfind("---", 3)] + "---\n" + body
            page_path.write_text(existing, encoding="utf-8")
            return str(page_path)

    page_path.write_text(content, encoding="utf-8")
    return str(page_path)


def extract_sources_from_answer(answer: str) -> list[str]:
    """Extract source URLs from the answer text itself."""
    return list(dict.fromkeys(re.findall(r"https?://[^\s\)\]>]+", answer)))
