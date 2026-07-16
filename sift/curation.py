"""Previewable, idempotent curation of raw Sift captures into an LLM wiki."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

from sift.wiki import slugify


class CurationError(RuntimeError):
    """Raised when the vault contract cannot be safely satisfied."""


@dataclass
class RawCapture:
    """Immutable raw query capture plus provenance digest."""

    path: Path
    metadata: dict[str, Any]
    body: str
    digest: str


@dataclass
# pylint: disable=too-many-instance-attributes
class CurationPlan:
    """Deterministic file and metadata changes for one raw capture."""
    source: RawCapture
    title: str
    page_type: str
    slug: str
    content: str
    links: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    target: Path | None = None

    @property
    def changed(self) -> bool:
        return self.target is None or not self.target.exists() or self.target.read_text(encoding="utf-8") != self.content


class EndpointSynthesizer:
    """OpenAI-compatible JSON endpoint; response must contain a JSON object."""

    def __init__(self, url: str | None = None, model: str | None = None, api_key: str | None = None):
        # Curation must opt in explicitly; a generic OPENAI_BASE_URL may belong to
        # the interactive synthesizer and can make deterministic tests or dry-runs
        # unexpectedly call an authenticated remote endpoint.
        self.url = url or os.environ.get("SIFT_CURATE_URL")
        self.model = model or os.environ.get("SIFT_CURATE_MODEL") or os.environ.get("OPENAI_MODEL", "")
        self.api_key = api_key or os.environ.get("SIFT_CURATE_API_KEY") or os.environ.get("OPENAI_API_KEY")

    def __call__(self, capture: RawCapture) -> dict[str, Any]:
        if not self.url:
            return _heuristic_synthesis(capture)
        import httpx
        prompt = ("Return JSON only with keys title, type (concept or entity), summary, "
                  "body, links (array of slugs), and claims (array of strings). Never include reasoning.\n\n"
                  + capture.body[:12000])
        payload = {"model": self.model, "messages": [{"role": "system", "content": prompt}], "temperature": 0.1}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            response = httpx.post(self.url, json=payload, headers=headers, timeout=120)
            response.raise_for_status()
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise CurationError(f"curation provider failed: {exc}") from exc
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise CurationError("curation provider returned non-JSON output") from exc


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        raise CurationError("raw capture has no frontmatter")
    end = text.find("\n---", 3)
    if end < 0:
        raise CurationError("raw capture frontmatter is unterminated")
    metadata: dict[str, Any] = {}
    active_list: str | None = None
    for line in text[4:end].splitlines():
        if line.startswith("  - ") and active_list:
            raw_value = line[4:].strip()
            try:
                value = json.loads(raw_value)
            except json.JSONDecodeError:
                value = raw_value
            metadata.setdefault(active_list, []).append(value)
            continue
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        key, value = key.strip(), value.strip()
        active_list = key if not value else None
        if not value:
            continue
        try:
            metadata[key] = json.loads(value)
        except json.JSONDecodeError:
            metadata[key] = value.strip('"')
    if "source" in metadata:
        metadata["source_urls"] = metadata.pop("source")
    return metadata, text[end + 4:].strip()


def read_capture(path: Path) -> RawCapture:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    metadata, body = _parse_frontmatter(text)
    if metadata.get("source_queries"):
        metadata["source_query"] = metadata["source_queries"]
    metadata.setdefault(
        "source_urls",
        list(dict.fromkeys(re.findall(r"https?://[^\s\)\]>\"']+", text))),
    )
    return RawCapture(path, metadata, body, hashlib.sha256(raw).hexdigest())


def _query_display(metadata: dict[str, Any]) -> str:
    """Render scalar or accumulated query provenance without Python reprs."""
    value = metadata.get("source_query", "")
    values = value if isinstance(value, list) else [value]
    rendered = "; ".join(str(item).strip() for item in values if str(item).strip())
    return rendered or "incomplete: source_query missing"


def _yaml_string(value: str) -> str:
    """Encode a scalar safely as a YAML double-quoted string."""
    return json.dumps(value, ensure_ascii=False)


def _inline_text(value: str) -> str:
    """Keep provenance readable without allowing Markdown control characters."""
    return " ".join(value.replace("`", "'").split())


def _provenance_lines(capture: RawCapture, vault: Path) -> list[str]:
    """Render complete provenance for a capture added to a curated page."""
    try:
        raw_path = capture.path.resolve().relative_to(vault.resolve()).as_posix()
    except ValueError:
        raw_path = capture.path.resolve().as_posix()
    query_display = _query_display(capture.metadata)
    source_urls = capture.metadata.get("source_urls", []) or [
        capture.metadata.get("source_url", "")
    ]
    lines = [f"- Raw capture: `{_inline_text(raw_path)}`",
             f"- Source hash: `{capture.digest}`",
             f"- Query: `{_inline_text(query_display)}`"]
    lines.extend(f"- Source URL: {_inline_text(str(url))}"
                 for url in source_urls if str(url).strip())
    return lines


def _validate_raw_capture(path: Path, capture: RawCapture, vault: Path) -> None:
    """Reject curated pages before they can be fed back into curation."""
    resolved = path.resolve()
    for root in (vault / "20-knowledge-tech", vault / "30-knowledge-spiritual", vault / "40-entities"):
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            continue
        raise CurationError(
            f"refusing curated page {path}: --file must point to a raw capture "
            "under raw/queries (frontmatter type: raw-source)"
        )
    declared_type = str(capture.metadata.get("type", "")).strip().lower()
    raw_roots = (vault / "raw" / "queries", vault / "80-raw" / "82-queries")
    in_raw_root = any(resolved.is_relative_to(root.resolve()) for root in raw_roots)
    if not in_raw_root and declared_type != "raw-source":
        raise CurationError(
            f"refusing {path}: input is not under raw/queries and is not marked "
            "type: raw-source; pass the original raw capture"
        )
    if declared_type and declared_type != "raw-source":
        raise CurationError(
            f"refusing {path.name}: frontmatter type is {declared_type!r}, not "
            "raw-source; pass the original file from raw/queries instead"
        )


def _clean_model_text(text: str) -> str:
    """Keep answer text, excluding model traces and rendered source sections."""
    text = re.sub(r"^---\s*\n.*?\n---\s*\n", "", text.strip(), count=1, flags=re.S)
    text = re.sub(r"<details>.*?</details>", "", text, flags=re.S | re.I)
    text = re.split(r"^##\s+(?:Sources|Sift curation update)\s*$", text,
                    maxsplit=1, flags=re.M | re.I)[0]
    lines = text.strip().splitlines()
    cleaned: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if re.match(r"^Thinking(?:\.{1,3}|:)?.*", stripped, re.I):
            skipping = True
            continue
        final_match = re.match(r"^(?:Final answer|Answer)\s*:?\s*(.*)$", stripped, re.I)
        if final_match:
            skipping = False
            if final_match.group(1):
                cleaned.append(final_match.group(1))
            continue
        if re.match(r"^(?:Reasoning\.?|Analysis:?)$", stripped, re.I):
            skipping = True
            continue
        if re.match(r"^\d+[.)]\s+(?:\*\*)?\s*(Analyze|Analysis|Context|Draft|Review|Reason|Synthesize|Refine)\b", stripped, re.I):
            skipping = True
            continue
        if not skipping:
            cleaned.append(line)
    return "\n".join(cleaned).strip()


def _heuristic_synthesis(capture: RawCapture) -> dict[str, Any]:
    title = capture.metadata.get("title") or capture.path.stem.replace("-", " ").title()
    clean = _clean_model_text(capture.body)
    return {"title": title, "type": "concept", "summary": clean.split("\n", 1)[0][:240],
            "body": clean, "links": [], "claims": []}


def _page_content(result: dict[str, Any], capture: RawCapture, links: list[str],
                  conflicts: list[str], vault: Path) -> str:
    today = date.today().isoformat()
    title = str(result["title"]).strip()
    title_yaml = _yaml_string(title)
    tags = ["topic:research", "workflow:curated"]
    lines = ["---", f"title: {title_yaml}", f"created: {today}", f"updated: {today}",
             f"type: {result['type']}", "tags:"] + [f"  - {tag}" for tag in tags]
    lines += ["workflow: curated", "confidence: medium", "sources:"]
    source_urls = capture.metadata.get("source_urls", []) or [capture.metadata.get("source_url", "")]
    source_urls = [str(url) for url in source_urls if str(url).strip()]
    query_display = _query_display(capture.metadata)
    for url in source_urls:
        lines += [f"  - url: {_yaml_string(url)}",
                  f"    query: {_yaml_string(query_display)}",
                  f"    captured: {_yaml_string(str(capture.metadata.get('ingested', today)))}",
                  f"    sha256: {capture.digest}"]
    safe_title = _inline_text(title)
    if conflicts:
        lines += ["contradictions:"] + [f'  - claim: {_yaml_string(_inline_text(item))}\n    resolution: pending' for item in conflicts]
    lines += ["---", "", f"# {safe_title}", "", _normalize_markdown(str(result.get("body", ""))), "", "## Sources", "",
              *_provenance_lines(capture, vault)]
    if links:
        lines += ["", "## Related", ""] + [f"- [[{link}]]" for link in links]
    return "\n".join(lines).rstrip() + "\n"


def _normalize_markdown(text: str) -> str:
    """Join wrapped prose while leaving Markdown block structure safe."""
    lines = text.strip().splitlines()
    output: list[str] = []
    in_fence = False

    def is_structural(line: str) -> bool:
        stripped = line.lstrip()
        return (not stripped or stripped.startswith(("#", "- ", "* ", "+ ", ">", "```", "~~~", "|"))
                or bool(re.match(r"\d+[.)]\s", stripped)))

    for line in lines:
        fence = line.lstrip().startswith(("```", "~~~"))
        if fence:
            in_fence = not in_fence
            output.append(line)
            continue
        if in_fence or not output or is_structural(line) or is_structural(output[-1]):
            output.append(line)
            continue
        previous = output[-1]
        if previous.endswith("\\") or previous.endswith("  "):
            output.append(line)
        else:
            output[-1] = previous.rstrip() + " " + line.strip()
    return "\n".join(output)


def _existing_pages(vault: Path) -> dict[str, Path]:
    pages = {}
    for root in (vault / "20-knowledge-tech", vault / "30-knowledge-spiritual", vault / "40-entities"):
        if root.exists():
            for path in root.rglob("*.md"):
                pages[path.stem.lower()] = path
    return pages


def plan_curation(raw_path: Path, vault: Path, synthesizer: Callable[[RawCapture], dict[str, Any]] | None = None) -> list[CurationPlan]:
    schema = vault / "10-system" / "11-meta" / "11.01 SCHEMA.md"
    index = vault / "10-system" / "11-meta" / "11.02 index.md"
    log = vault / "10-system" / "11-meta" / "11.03 log.md"
    if not schema.exists() or not index.exists() or not log.exists():
        raise CurationError("vault schema, index, and log are all required")
    schema_text = schema.read_text(encoding="utf-8")
    if "frontmatter" not in schema_text.lower() or "workflow" not in schema_text.lower():
        raise CurationError("vault schema does not describe required frontmatter/workflow conventions")
    index.read_text(encoding="utf-8")
    log.read_text(encoding="utf-8")
    synth = synthesizer or EndpointSynthesizer()
    existing = _existing_pages(vault)
    plans = []
    seen_slugs: dict[str, Path] = {}
    if raw_path.is_file():
        if raw_path.suffix.lower() != ".md":
            raise CurationError("curation input must be a Markdown file")
        capture_paths = [raw_path]
    elif raw_path.is_dir():
        capture_paths = sorted(raw_path.glob("*.md"))
    else:
        raise CurationError(f"curation input does not exist: {raw_path}")
    for path in capture_paths:
        capture = read_capture(path)
        _validate_raw_capture(path, capture, vault)
        result = synth(capture)
        result["body"] = _clean_model_text(str(result.get("body", "")))
        page_type = str(result.get("type", "concept")).lower()
        if page_type not in {"concept", "entity"} or not result.get("title") or not result.get("body"):
            raise CurationError(f"provider result for {path.name} is missing a concept/entity title/body")
        slug = slugify(str(result["title"]))
        if slug in seen_slugs:
            raise CurationError(
                f"duplicate synthesized slug {slug!r} for "
                f"{seen_slugs[slug].name} and {path.name}"
            )
        seen_slugs[slug] = path
        links = sorted({slugify(str(link)) for link in result.get("links", []) if slugify(str(link)) and slugify(str(link)) != slug})
        conflicts = []
        if not str(capture.metadata.get("source_query", "")).strip():
            conflicts.append("Provenance incomplete: raw capture has no source_query")
        conflicts.extend(str(item) for item in result.get("conflicts", result.get("claims", [])) if str(item).strip())
        target = existing.get(slug)
        content = _page_content(result, capture, links, conflicts, vault)
        if target:
            existing_text = target.read_text(encoding="utf-8")
            if capture.digest in existing_text:
                content = existing_text
            else:
                conflicts.append(f"Existing page {target.name} has prior claims; appended, not overwritten")
                content = (existing_text.rstrip() + "\n\n## Sift curation update\n\n"
                           + _normalize_markdown(str(result["body"])) + "\n\n"
                           + "\n".join(_provenance_lines(capture, vault)) + "\n")
        plans.append(CurationPlan(capture, str(result["title"]), page_type, slug,
                                  content, links, conflicts, target))
    return plans


def apply_curation(plans: list[CurationPlan], vault: Path, dry_run: bool = False) -> dict[str, Any]:
    actions = {"created": [], "updated": [], "unchanged": [], "files": [], "links": [], "conflicts": []}
    for plan in plans:
        target = plan.target or vault / ("40-entities" if plan.page_type == "entity" else "20-knowledge-tech/21-ai-concepts") / f"{plan.slug}.md"
        actions["files"].append(str(target.relative_to(vault)))
        action = "unchanged" if not plan.changed else ("updated" if plan.target else "created")
        actions[action].append(plan.slug)
        actions["links"].extend(f"{plan.slug} -> {link}" for link in plan.links)
        actions["conflicts"].extend(plan.conflicts)
    if dry_run or not plans:
        return actions
    index = vault / "10-system" / "11-meta" / "11.02 index.md"
    log = vault / "10-system" / "11-meta" / "11.03 log.md"
    originals = {p: p.read_text(encoding="utf-8") for p in [index, log]}
    page_originals: dict[Path, str | None] = {}
    try:
        for plan in plans:
            if not plan.changed:
                continue
            target = plan.target or vault / ("40-entities" if plan.page_type == "entity" else "20-knowledge-tech/21-ai-concepts") / f"{plan.slug}.md"
            page_originals[target] = target.read_text(encoding="utf-8") if target.exists() else None
            target.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(target, plan.content)
            plan.target = target
        index_text = index.read_text(encoding="utf-8")
        additions = [f"- [[{p.slug}]] — {p.title}" for p in plans if p.slug not in index_text]
        if additions:
            _atomic_write(index, index_text.rstrip() + "\n\n## Curated by Sift\n" + "\n".join(additions) + "\n")
        log_text = log.read_text(encoding="utf-8")
        entries = [f"- {p.slug}: `{p.source.path.name}` sha256 `{p.source.digest}`" for p in plans if p.slug not in log_text]
        if entries:
            _atomic_write(log, log_text.rstrip() + f"\n\n## {date.today().isoformat()} — Sift automatic curation\n" + "\n".join(entries) + "\n")
    except Exception:
        for path, text in originals.items():
            _atomic_write(path, text)
        for path, text in page_originals.items():
            if text is None:
                path.unlink(missing_ok=True)
            else:
                _atomic_write(path, text)
        raise
    return actions


def _atomic_write(path: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        handle.write(content)
        temp = Path(handle.name)
    os.replace(temp, path)
