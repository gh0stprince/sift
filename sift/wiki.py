"""Wiki output helpers — write sift ask results to the LLM wiki."""

from __future__ import annotations

import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

WIKI_RAW_DIR = Path.home() / "llm-wiki" / "raw" / "queries"


def _yaml_string(value: str) -> str:
    """Encode a scalar as a YAML-compatible JSON double-quoted string."""
    return json.dumps(value, ensure_ascii=False)


def _atomic_write(path: Path, content: str) -> None:
    """Replace *path* atomically with UTF-8 *content*."""
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="",
        prefix=f".{path.name}.",
        dir=path.parent,
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _decode_frontmatter_value(value: str) -> str:
    """Decode the JSON-backed scalar form used by generated frontmatter."""
    try:
        decoded = json.loads(value.strip())
    except json.JSONDecodeError:
        decoded = value.strip().strip('"')
    return str(decoded)


def _frontmatter_list(frontmatter: str, key: str) -> list[str]:
    """Return one generated scalar-list property from *frontmatter*."""
    match = re.search(rf"(?m)^{re.escape(key)}:\n((?:  - .*\n?)*)", frontmatter)
    if not match:
        return []
    return [
        _decode_frontmatter_value(line[4:])
        for line in match.group(1).splitlines()
        if line.startswith("  - ")
    ]


def _merge_frontmatter_provenance(
    frontmatter: str, query: str, sources: list[str]
) -> str:
    """Merge appended query/source provenance into opening frontmatter."""
    queries = _frontmatter_list(frontmatter, "source_queries")
    if not queries:
        match = re.search(r"(?m)^source_query:\s*(.*)$", frontmatter)
        if match:
            queries.append(_decode_frontmatter_value(match.group(1)))
    if query not in queries:
        queries.append(query)
    query_block = "source_queries:\n" + "\n".join(
        f"  - {_yaml_string(value)}" for value in queries
    )
    if re.search(r"(?m)^source_queries:\n", frontmatter):
        frontmatter = re.sub(
            r"(?m)^source_queries:\n(?:  - .*\n?)*",
            lambda _match: query_block + "\n",
            frontmatter,
            count=1,
        ).rstrip()
    else:
        frontmatter = re.sub(
            r"(?m)^(source_query:.*)$",
            lambda match: f"{match.group(1)}\n{query_block}",
            frontmatter,
            count=1,
        )

    merged_sources = list(
        dict.fromkeys([*_frontmatter_list(frontmatter, "source"), *sources])
    )
    if merged_sources:
        source_block = "source:\n" + "\n".join(
            f"  - {_yaml_string(value)}" for value in merged_sources
        )
        if re.search(r"(?m)^source:\n", frontmatter):
            frontmatter = re.sub(
                r"(?m)^source:\n(?:  - .*\n?)*",
                lambda _match: source_block + "\n",
                frontmatter,
                count=1,
            ).rstrip()
        else:
            frontmatter = f"{frontmatter.rstrip()}\n{source_block}"
    return frontmatter


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

    # Use local split to extract answer from reasoning (avoids extra LLM call)
    clean_answer, reasoning = split_answer_reasoning(synthesis)
    summary = ""

    # Build body - use LLM-cleaned answer
    body = clean_answer

    # Build frontmatter
    tag_lines = "  - topic:research"

    # Add summary as description if we got one
    summary_line = f"description: {_yaml_string(summary[:100])}\n" if summary else ""

    frontmatter = f"""---
title: {_yaml_string(title)}
created: {today}
updated: {today}
type: raw-source
{summary_line}tags:
{tag_lines}
source_query: {_yaml_string(query)}
ingested: {today}
"""

    if sources:
        source_lines = "\n".join(f"  - {_yaml_string(s)}" for s in sources[:10])
        frontmatter += f"source:\n{source_lines}\n"

    frontmatter += "---"

    # Add raw reasoning as appendix if present
    if reasoning:
        body += f"\n\n<details>\n<summary>Raw reasoning (model thinking)</summary>\n\n{reasoning}\n</details>"

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
        fm_end = existing.find("\n---\n", 4)
        if existing.startswith("---\n") and fm_end > 0:
            frontmatter = existing[:fm_end]
            updated_frontmatter, count = re.subn(
                r"(?m)^updated:\s*.*$", f"updated: {today}", frontmatter, count=1
            )
            if count == 0:
                updated_frontmatter += f"\nupdated: {today}"
            updated_frontmatter = _merge_frontmatter_provenance(
                updated_frontmatter, query, sources
            )
            existing_body = existing[fm_end + len("\n---\n"):].rstrip()
            updated = (
                f"{updated_frontmatter}\n---\n\n{existing_body}"
                f"\n\n---\n\n## Update - {today}\n\n{body}\n"
            )
            _atomic_write(page_path, updated)
            return str(page_path)

    _atomic_write(page_path, content)
    return str(page_path)


def extract_sources_from_answer(answer: str) -> list[str]:
    """Extract source URLs from the answer text itself."""
    return list(dict.fromkeys(re.findall(r"https?://[^\s\)\]>]+", answer)))
