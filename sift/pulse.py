"""Pulse engine for web research via DuckDuckGo search and page crawling."""

from __future__ import annotations

import json
import logging
import time
import warnings
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin

import requests
import trafilatura

# Suppress rename warning from ddgs package — we know.
warnings.filterwarnings("ignore", message=".*ddgs.*renamed.*")
from ddgs import DDGS

from sift.robots import RobotsPolicy

logger = logging.getLogger(__name__)


class _LinkExtractor(HTMLParser):
    """HTML parser that collects all href attributes from <a> tags."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value is not None:
                self.links.append(value)


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

    SOCIAL_DOMAINS = frozenset({
        "facebook.com",
        "www.facebook.com",
        "twitter.com",
        "www.twitter.com",
        "x.com",
        "www.x.com",
        "linkedin.com",
        "www.linkedin.com",
        "instagram.com",
        "www.instagram.com",
    })

    def __init__(self, db: Any, user_agent: str | None = None) -> None:
        self.db = db
        self.session = requests.Session()
        configured_user_agent = user_agent or "Sift/0.1.0 (+https://github.com/gh0st/sift)"
        self.session.headers.update({
            "User-Agent": configured_user_agent,
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
        })
        self.robots = RobotsPolicy(self.session, configured_user_agent)
        self.robots_skipped: dict[str, int] = {}
        self.ddgs = DDGS()

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
            resp = self.session.get(url, timeout=30)
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

    def _extract_links(self, html: str, base_url: str) -> list[str]:
        """Parse HTML and return absolute http/s URLs, excluding social media."""
        parser = _LinkExtractor()
        try:
            parser.feed(html)
        except Exception:
            return []

        out: list[str] = []
        for href in parser.links:
            absolute = urljoin(base_url, href)
            # Keep only http/s URLs
            if not absolute.startswith(("http://", "https://")):
                continue
            # Filter out social media domains
            parsed = absolute.split("/")[2] if "://" in absolute else ""
            if parsed in self.SOCIAL_DOMAINS:
                continue
            out.append(absolute)

        return out

    # ------------------------------------------------------------------
    # Main run method
    # ------------------------------------------------------------------

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
        # Step 1: Create pulse record
        cur = self.db.conn.execute(
            "INSERT INTO pulses (query, depth) VALUES (?, ?)",
            (query, depth),
        )
        self.db.conn.commit()
        pulse_id = cur.lastrowid

        # Step 2: Generate query variations
        variations = self._generate_query_variations(query)

        # Step 3: Phase 1 — search DDG for each variation.
        # Collect all URLs into a dict keyed by URL.
        url_map: dict[str, dict[str, Any]] = {}
        for v in variations:
            results = self._search_ddg(v, max_results=10)
            for r in results:
                u = r["url"]
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

        # Step 4: Phase 2 — fetch top 15 pages at depth 0
        pages_stored = 0
        stored_pages: list[dict[str, Any]] = []

        for url, _info in ranked[:15]:
            if pages_stored >= max_pages:
                break
            result = self._fetch_and_store(url, pulse_id, link_depth=0)
            if result is not None:
                pages_stored += 1
                stored_pages.append(result)
            time.sleep(0.5)

        # Step 5: Phase 3 — crawl depth-2 links from top 5 stored pages
        if depth >= 2 and pages_stored < max_pages:
            for stored in stored_pages[:5]:
                if pages_stored >= max_pages:
                    break
                # Use stored HTML to extract links (already fetched in Phase 2)
                html = stored.get("html")
                if html is None:
                    continue
                links = self._extract_links(html, stored["url"])
                for link in links[:5]:
                    if pages_stored >= max_pages:
                        break
                    if not self._allowed(link):
                        continue
                    result = self._fetch_and_store(
                        link, pulse_id, link_depth=1
                    )
                    if result is not None:
                        pages_stored += 1
                    time.sleep(0.5)

        # Step 6: Update pulse record
        self.db.conn.execute(
            "UPDATE pulses SET finished_at=datetime('now'),"
            " pages_found=? WHERE id=?",
            (pages_stored, pulse_id),
        )
        self.db.conn.commit()

        # Step 7: Return result
        return {
            "pulse_id": pulse_id,
            "query": query,
            "pages_found": pages_stored,
            "total_depth": depth,
            "robots_skipped": sum(self.robots_skipped.values()),
            "robots_skip_reasons": dict(self.robots_skipped),
        }
