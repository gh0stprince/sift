"""Feed fetcher for RSS/Atom feed ingestion and page content extraction."""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from typing import Any

import requests
import trafilatura


class FeedFetcher:
    """Fetches and parses RSS/Atom feeds, extracts page content via trafilatura.

    Parameters
    ----------
    db_instance : Any
        A database instance providing ``get_sources()``, ``add_source()``,
        ``page_exists()``, and ``add_page()`` methods.
    user_agent : str | None
        Custom User-Agent header value.  Falls back to a sensible default.
    """

    def __init__(self, db_instance: Any, user_agent: str | None = None) -> None:
        self.db = db_instance
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent
                or "Sift/0.1.0 (+https://github.com/gh0st/sift)",
                "Accept": (
                    "application/rss+xml, application/atom+xml,"
                    " text/xml, application/xml"
                ),
            }
        )

    # ------------------------------------------------------------------
    # Feed source management
    # ------------------------------------------------------------------

    def list_feeds(self) -> list[dict[str, Any]]:
        """Return all feed sources from the database."""
        return self.db.get_sources()

    def add_feed(self, name: str, url: str) -> None:
        """Register a new feed source."""
        self.db.add_source(name=name, url=url, kind="feed")

    # ------------------------------------------------------------------
    # Feed fetching and parsing
    # ------------------------------------------------------------------

    def fetch_feed(self, feed_url: str) -> list[dict[str, str]]:
        """Fetch and parse a single RSS or Atom feed.

        Returns a list of ``{"url": ..., "title": ...}`` dicts, one per
        item/entry found in the feed.
        """
        resp = self.session.get(feed_url, timeout=30)
        resp.raise_for_status()
        return self._parse_feed_xml(resp.text)

    @staticmethod
    def _parse_feed_xml(xml_text: str) -> list[dict[str, str]]:
        """Parse RSS (``<rss><channel><item>``) or Atom (``<feed><entry>``).

        Uses ``xml.etree.ElementTree`` (stdlib) -- no external XML library
        required.
        """
        root = ET.fromstring(xml_text)
        entries: list[dict[str, str]] = []

        # --- RSS 2.0 ---
        channel = root.find("channel")
        if channel is not None:
            for item in channel.findall("item"):
                link_el = item.find("link")
                title_el = item.find("title")
                url = (link_el.text or "").strip() if link_el is not None else ""
                title = (
                    (title_el.text or "").strip() if title_el is not None else ""
                )
                if url:
                    entries.append({"url": url, "title": title})
            return entries

        # --- Atom ---
        # Atom feeds use a namespace; detect it by scanning the raw text
        # rather than relying on ElementTree's clumsy prefix handling.
        atom_ns = (
            "http://www.w3.org/2005/Atom"
            if "http://www.w3.org/2005/Atom" in xml_text
            else None
        )
        if atom_ns:
            entry_tag = f"{{{atom_ns}}}entry"
            title_tag = f"{{{atom_ns}}}title"
            link_tag = f"{{{atom_ns}}}link"

            for entry_el in root.findall(f".//{entry_tag}"):
                title_el = entry_el.find(title_tag)
                title = (
                    (title_el.text or "").strip()
                    if title_el is not None
                    else ""
                )

                # Prefer ``rel="alternate"``, fall back to first href
                url = ""
                for link_el in entry_el.findall(link_tag):
                    href = (link_el.get("href") or "").strip()
                    if not href:
                        continue
                    rel = link_el.get("rel", "alternate")
                    if rel == "alternate":
                        url = href
                        break
                    if not url:
                        url = href

                if url:
                    entries.append({"url": url, "title": title})

            return entries

        return entries  # unrecognised format

    # ------------------------------------------------------------------
    # Single-page content extraction
    # ------------------------------------------------------------------

    def fetch_page(self, url: str) -> dict[str, Any] | None:
        """Download *url* and extract readable content with trafilatura.

        Returns ``{"url": ..., "title": ..., "content": ...}`` or ``None``
        when the page is unreachable or extraction fails.
        """
        try:
            resp = self.session.get(url, timeout=30)
            resp.raise_for_status()
        except requests.RequestException:
            return None

        try:
            doc = trafilatura.extract_with_metadata(
                resp.text,
                output_format="json",
                include_links=True,
            )
        except Exception:
            return None

        if doc is None:
            return None

        # With output_format="json", doc.text is a JSON string containing
        # the full metadata payload.
        try:
            data: dict[str, Any] = json.loads(doc.text) if doc.text else {}
        except (json.JSONDecodeError, TypeError):
            return None

        return {
            "url": url,
            "title": data.get("title") or "",
            "content": data.get("text") or "",
        }

    # ------------------------------------------------------------------
    # Batch processing
    # ------------------------------------------------------------------

    def run_all(self, max_per_feed: int = 10) -> dict[str, int]:
        """Iterate every registered feed, fetch new entries, and persist.

        For each feed:
        1. Fetch and parse the feed XML (up to *max_per_feed* entries).
        2. For each entry, check whether its URL already exists in the DB;
           if so, skip it.
        3. Otherwise fetch the page, extract content, and store via
           ``db.add_page()``.
        4. Sleep 1 second between page fetches to be polite.

        Returns
        -------
        dict[str, int]
            Statistics dict with keys ``feeds_checked``, ``pages_fetched``,
            ``pages_skipped``, and ``errors``.
        """
        feeds = self.list_feeds()
        stats: dict[str, int] = {
            "feeds_checked": 0,
            "pages_fetched": 0,
            "pages_skipped": 0,
            "errors": 0,
        }

        for feed in feeds:
            feed_url = feed.get("url", "")
            if not feed_url:
                continue

            stats["feeds_checked"] += 1

            try:
                entries = self.fetch_feed(feed_url)
            except Exception:
                stats["errors"] += 1
                continue

            for entry in entries[:max_per_feed]:
                entry_url = entry.get("url", "")
                if not entry_url:
                    continue

                # Skip already-known URLs
                try:
                    if self.db.page_exists(entry_url):
                        stats["pages_skipped"] += 1
                        continue
                except Exception:
                    pass  # treat DB error as "not found" and try anyway

                # Fetch and store
                try:
                    page_data = self.fetch_page(entry_url)
                    if page_data is not None:
                        self.db.add_page(
                            url=page_data["url"],
                            title=page_data["title"],
                            content=page_data["content"],
                        )
                        stats["pages_fetched"] += 1
                    else:
                        stats["errors"] += 1
                except Exception:
                    stats["errors"] += 1

                time.sleep(1)  # rate-limit: be polite to servers

        return stats
