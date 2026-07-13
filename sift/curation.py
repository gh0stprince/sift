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
        self.url = url or os.environ.get("SIFT_CURATE_URL") or os.environ.get("OPENAI_BASE_URL")
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
    for line in text[4:end].splitlines():
        if ":" not in line or line.startswith(" "):
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip('"')
        if value:
            metadata[key.strip()] = value
    return metadata, text[end + 4:].strip()


def read_capture(path: Path) -> RawCapture:
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    metadata, body = _parse_frontmatter(text)
    metadata["source_urls"] = list(dict.fromkeys(re.findall(r"https?://[^\s\)\]>]+", text)))
    return RawCapture(path, metadata, body, hashlib.sha256(raw).hexdigest())


def _heuristic_synthesis(capture: RawCapture) -> dict[str, Any]:
    title = capture.metadata.get("title") or capture.path.stem.replace("-", " ").title()
    clean = re.sub(r"<details>.*?</details>", "", capture.body, flags=re.S).strip()
    clean = re.sub(r"\n---\n.*", "", clean, flags=re.S).strip()
    return {"title": title, "type": "concept", "summary": clean.split("\n", 1)[0][:240],
            "body": clean, "links": [], "claims": []}


def _yaml_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict)) else str(value)


def _page_content(result: dict[str, Any], capture: RawCapture, links: list[str], conflicts: list[str]) -> str:
    today = date.today().isoformat()
    title = str(result["title"]).strip()
    tags = ["topic:research", "workflow:curated"]
    lines = ["---", f'title: "{title.replace(chr(34), chr(39))}"', f"created: {today}", f"updated: {today}",
             f"type: {result['type']}", "tags:"] + [f"  - {tag}" for tag in tags]
    lines += ["workflow: curated", "confidence: medium", "sources:"]
    source_urls = capture.metadata.get("source_urls", []) or [capture.metadata.get("source_url", "")]
    for url in source_urls:
        lines += [f'  - url: "{url}"',
                  f'    query: "{capture.metadata.get("source_query", "")}"',
                  f"    captured: {capture.metadata.get('ingested', today)}",
                  f"    sha256: {capture.digest}"]
    if conflicts:
        lines += ["contradictions:"] + [f'  - claim: "{item.replace(chr(34), chr(39))}"\n    resolution: pending' for item in conflicts]
    lines += ["---", "", f"# {title}", "", str(result.get("body", "")).strip(), "", "## Sources", "",
              f"- Raw capture: `raw/queries/{capture.path.name}`", f"- Query: `{capture.metadata.get('source_query', '')}`"]
    if links:
        lines += ["", "## Related", ""] + [f"- [[{link}]]" for link in links]
    return "\n".join(lines).rstrip() + "\n"


def _existing_pages(vault: Path) -> dict[str, Path]:
    pages = {}
    for root in (vault / "20-knowledge-tech", vault / "30-knowledge-spiritual", vault / "40-entities"):
        if root.exists():
            for path in root.rglob("*.md"):
                pages[path.stem.lower()] = path
    return pages


def plan_curation(raw_dir: Path, vault: Path, synthesizer: Callable[[RawCapture], dict[str, Any]] | None = None) -> list[CurationPlan]:
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
    seen_slugs: set[str] = set()
    for path in sorted(raw_dir.glob("*.md")):
        capture = read_capture(path)
        result = synth(capture)
        page_type = str(result.get("type", "concept")).lower()
        if page_type not in {"concept", "entity"} or not result.get("title") or not result.get("body"):
            raise CurationError(f"provider result for {path.name} is missing a concept/entity title/body")
        slug = slugify(str(result["title"]))
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        links = sorted({slugify(str(link)) for link in result.get("links", []) if slugify(str(link)) and slugify(str(link)) != slug})
        conflicts = [str(item) for item in result.get("conflicts", result.get("claims", [])) if str(item).strip()]
        target = existing.get(slug)
        content = _page_content(result, capture, links, conflicts)
        if target:
            existing_text = target.read_text(encoding="utf-8")
            if capture.digest in existing_text:
                content = existing_text
            else:
                conflicts.append(f"Existing page {target.name} has prior claims; appended, not overwritten")
                content = (existing_text.rstrip() + "\n\n## Sift curation update\n\n"
                           + str(result["body"]).strip() + "\n\n"
                           + f"Source hash: `{capture.digest}`\n")
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
