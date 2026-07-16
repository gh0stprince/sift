"""Pulse engine for web research via DuckDuckGo search and page crawling."""

from __future__ import annotations

import json
import logging
import time
import warnings
from collections import deque
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests
import trafilatura

# Suppress rename warning from ddgs package — we know.
warnings.filterwarnings("ignore", message=".*ddgs.*renamed.*")
from ddgs import DDGS

from sift.links import extract_links
from sift.outbound import OutboundPolicy, PinnedSession, safe_get
from sift.robots import RobotsPolicy

logger = logging.getLogger(__name__)


class PulseEngine:
    """Orchestrates web research: query variation generation, DDG search,
    page fetching with trafilatura, and depth-limited crawling.

    Parameters
    ----------
    db : Any
        Database instance providing ``add_page()``, ``conn``, etc.
    user_agent : str | None
        User-Agent header value.
    """

    def __init__(
        self,
        db: Any,
        user_agent: str | None = None,
        *,
        session=None,
        resolver=None,
        ddgs=None,
        sleeper=time.sleep,
    ) -> None:
        self.db = db
        self.session = session or PinnedSession()
        configured_user_agent = user_agent or "Sift/0.1.0 (+https://github.com/gh0st/sift)"
        self.session.headers.update({
            "User-Agent": configured_user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
        })
        self.url_policy = OutboundPolicy(resolver=resolver)
        self.robots = RobotsPolicy(
            self.session, configured_user_agent, url_policy=self.url_policy
        )
        self.robots_skipped: dict[str, int] = {}
        self.ddgs = ddgs or DDGS()
        self.sleeper = sleeper

    def close(self) -> None:
        """Release the Pulse HTTP session."""
        self.session.close()

    def __enter__(self) -> PulseEngine:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Query variation generation
    # ------------------------------------------------------------------

    def _generate_query_variations(self, query: str) -> list[str]:
        """Return 8 search-query variations for *query*."""
        quoted = f'"{query}"'
        return [
            query,
            quoted,
            f"what is {query}",
            f"{query} research 2026",
            f"{query} overview",
            f"{query} explained",
            f"{query} vs",
            f"{query} how does it work",
        ]

    # ------------------------------------------------------------------
    # DuckDuckGo search
    # ------------------------------------------------------------------

    def _search_ddg(
        self, query: str, max_results: int = 10
    ) -> list[dict[str, str]]:
        """Search DuckDuckGo and return up to *max_results* results.

        Returns a list of ``{"url": ..., "title": ..., "body": ...}`` dicts.
        On failure returns an empty list.
        """
        try:
            results = self.ddgs.text(query, max_results=max_results)
        except Exception as e:
            logger.warning(f"DDG search failed for '{query}': {e}")
            return []

        out: list[dict[str, str]] = []
        for r in results:
            out.append({
                "url": r.get("href", ""),
                "title": r.get("title", ""),
                "body": r.get("body", ""),
            })

        time.sleep(1)
        return out

    def _allowed(self, url: str) -> bool:
        """Check robots and record only a non-sensitive skip reason."""
        decision = self.robots.check(url)
        if not decision.allowed:
            self.robots_skipped[decision.reason] = (
                self.robots_skipped.get(decision.reason, 0) + 1
            )
        return decision.allowed

    # ------------------------------------------------------------------
    # Page fetching & storage


    def _fetch_and_store(
        self,
        url: str,
        pulse_id: int,
        source_id: int | None = None,
        link_depth: int = 0,
    ) -> dict[str, Any] | None:
        """Fetch *url*, extract content, and persist to the database.

        Returns ``{"id": ..., "url": ..., "title": ..., "content": ...}``
        or ``None`` on any error.
        """
        if not self._allowed(url):
            return None
        # Skip if already in DB
        try:
            cur = self.db.conn.execute(
                "SELECT 1 FROM pages WHERE url = ?", (url,)
            )
            if cur.fetchone() is not None:
                return None
        except Exception:
            pass

        # Fetch the page
        try:
            resp = safe_get(
                self.session,
                url,
                policy=self.url_policy,
                timeout=30,
                authorize=self._allowed,
            )
            resp.raise_for_status()
        except requests.RequestException:
            return None

        html = resp.text

        # Extract content with trafilatura
        try:
            doc = trafilatura.extract_with_metadata(
                html,
                output_format="json",
                include_links=True,
            )
        except Exception:
            return None

        if doc is None:
            return None

        try:
            data: dict[str, Any] = json.loads(doc.text) if doc.text else {}
        except (json.JSONDecodeError, TypeError):
            return None

        title = data.get("title") or ""
        content = data.get("text") or ""

        if len(content) < 50:
            return None

        # Store in DB
        try:
            page_id = self.db.add_page(
                url=url,
                title=title,
                content=content,
                source_id=source_id,
                pulse_id=pulse_id,
                link_depth=link_depth,
            )
        except Exception:
            return None

        return {
            "id": page_id,
            "url": url,
            "title": title,
            "content": content,
            "html": html,
        }

    # ------------------------------------------------------------------
    # Link extraction from HTML
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_links(html: str, base_url: str) -> list[str]:
        """Parse HTML and return absolute http/s URLs, excluding social media."""
        return extract_links(html, base_url)

    # ------------------------------------------------------------------
    # Main run method
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_url(url: str) -> str | None:
        """Return a fragment-free canonical HTTP(S) URL for deduplication."""
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except (TypeError, ValueError):
            return None
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        host = parsed.hostname.lower()
        if ":" in host:
            host = f"[{host}]"
        default_port = (parsed.scheme.lower() == "http" and port == 80) or (
            parsed.scheme.lower() == "https" and port == 443
        )
        netloc = host if port is None or default_port else f"{host}:{port}"
        return urlunsplit(
            (parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, "")
        )

    def _fetch_page(
        self, url: str, pulse_id: int, link_depth: int
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Fetch/store one page and return its outgoing links without refetching."""
        stored = self._fetch_and_store(url, pulse_id, link_depth=link_depth)
        if stored is None:
            return None, []
        return stored, self._extract_links(stored.get("html", ""), url)

    def run(
        self, query: str, depth: int = 2, max_pages: int = 30
    ) -> dict[str, Any]:
        """Execute a full pulse: DDG search + depth-limited crawling.

        Returns
        -------
        dict
            ``{"pulse_id": id, "query": query,
                "pages_found": n, "total_depth": depth}``
        """
        if not isinstance(depth, int) or not 0 <= depth <= 3:
            raise ValueError("depth must be between 0 and 3")
        if not isinstance(max_pages, int) or max_pages <= 0:
            raise ValueError("max_pages must be a positive integer")

        # Step 1: Create pulse record.
        pulse_id = self.db.add_pulse(query, depth)

        # Step 2: Generate query variations
        variations = self._generate_query_variations(query)

        # Step 3: Phase 1 — search DDG for each variation.
        # Collect all URLs into a dict keyed by URL.
        url_map: dict[str, dict[str, Any]] = {}
        for v in variations:
            results = self._search_ddg(v, max_results=10)
            for r in results:
                u = self._normalize_url(r["url"])
                if not u:
                    continue
                if u in url_map:
                    url_map[u]["count"] += 1
                else:
                    url_map[u] = {
                        "count": 1,
                        "title": r["title"],
                        "body": r["body"],
                    }

        # Rank by count descending
        ranked = sorted(
            url_map.items(), key=lambda x: x[1]["count"], reverse=True
        )

        # Step 4: Breadth-first traversal. Depth zero is search-only; depth one
        # fetches seed URLs; each higher value adds one outgoing-link level.
        pages_attempted = 0
        pages_stored = 0
        seen = {url for url, _info in ranked}
        seed_urls = deque(url for url, _info in ranked)
        seed_limit = max_pages if depth <= 1 else max(1, max_pages // depth)
        queue = deque(
            (seed_urls.popleft(), 0)
            for _unused in range(min(seed_limit, len(seed_urls)))
        )
        while (queue or seed_urls) and pages_attempted < max_pages:
            if not queue:
                queue.append((seed_urls.popleft(), 0))
            url, link_depth = queue.popleft()
            if link_depth >= depth:
                continue
            pages_attempted += 1
            stored, links = self._fetch_page(url, pulse_id, link_depth)
            if stored is None:
                continue
            pages_stored += 1
            self.sleeper(0.5)
            next_depth = link_depth + 1
            if next_depth >= depth:
                continue
            for link in links:
                normalized = self._normalize_url(link)
                if normalized is None or normalized in seen:
                    continue
                seen.add(normalized)
                queue.append((normalized, next_depth))

        # Step 6: Update pulse record
        self.db.finish_pulse(pulse_id, pages_stored)

        # Step 7: Return result
        return {
            "pulse_id": pulse_id,
            "query": query,
            "pages_found": pages_stored,
            "total_depth": depth,
            "urls_discovered": len(seen),
            "robots_skipped": sum(self.robots_skipped.values()),
            "robots_skip_reasons": dict(self.robots_skipped),
        }
