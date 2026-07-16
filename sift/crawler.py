"""Domain crawler with sitemap discovery and BFS fallback for Sift."""

from __future__ import annotations

import json
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

import requests
import trafilatura

from sift.links import extract_links
from sift.outbound import OutboundPolicy, PinnedSession, UnsafeURLError, safe_get
from sift.robots import RobotsPolicy


def _strip_ns(tag: str) -> str:
    """Strip XML namespace from *tag* (e.g. ``{ns}urlset`` → ``urlset``)."""
    idx = tag.find("}")
    return tag[idx + 1 :] if idx != -1 else tag


class DomainCrawler:
    """Crawl a domain via sitemap discovery or BFS link traversal.

    Parameters
    ----------
    db : Any
        Database instance providing ``add_source()`` and ``add_page()``.
    user_agent : str | None
        Custom User-Agent header value.
    """

    def __init__(
        self,
        db: Any,
        user_agent: str | None = None,
        *,
        session=None,
        resolver=None,
    ) -> None:
        self.db = db
        self.session = session or PinnedSession()
        configured_user_agent = user_agent or "Sift/0.1.0 (+https://github.com/gh0st/sift)"
        self.session.headers.update(
            {
                "User-Agent": configured_user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,"
                    "application/xml;q=0.9,*/*;q=0.8"
                ),
            }
        )
        self.url_policy = OutboundPolicy(resolver=resolver)
        self.robots = RobotsPolicy(
            self.session, configured_user_agent, url_policy=self.url_policy
        )
        self.skipped: dict[str, int] = {}

    def close(self) -> None:
        """Release the crawler's HTTP session."""
        self.session.close()

    def __enter__(self) -> DomainCrawler:
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    # ------------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_root(url: str) -> str:
        """Extract the scheme/host/port origin from *url*."""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"

    def _allowed(self, url: str) -> bool:
        """Check robots and record only a non-sensitive skip reason."""
        decision = self.robots.check(url)
        if not decision.allowed:
            self.skipped[decision.reason] = self.skipped.get(decision.reason, 0) + 1
        return decision.allowed

    def _crawl_allowed(self, url: str, root: str) -> bool:
        """Keep every crawl hop on the requested host and within robots policy."""
        return self._is_internal_url(url, root) and self._allowed(url)


    @staticmethod
    def _is_internal_url(url: str, root: str) -> bool:
        """Return whether *url* is an HTTP(S) URL on the crawl host."""
        parsed_url = urlparse(url)
        parsed_root = urlparse(root)
        return (
            parsed_url.scheme in {"http", "https"}
            and parsed_url.hostname is not None
            and parsed_root.hostname is not None
            and parsed_url.hostname.casefold() == parsed_root.hostname.casefold()
        )

    # ------------------------------------------------------------------
    # Sitemap discovery
    # ------------------------------------------------------------------

    def _discover_sitemaps(self, root: str) -> list[str]:
        """Discover sitemap URLs for *root*.

        Tries ``/robots.txt`` first (parsing ``Sitemap:`` directives),
        then falls back to the conventional ``/sitemap.xml`` and
        ``/sitemap_index.xml`` paths.

        Returns a list of sitemap URLs (possibly empty).
        """
        sitemaps: list[str] = list(self.robots.sitemaps(root))
        sitemaps = [
            url for url in sitemaps if self._is_internal_url(url, root)
        ]
        if sitemaps:
            return sitemaps

        # Fall back to common sitemap paths, but obey robots for each one.
        for path in ("/sitemap.xml", "/sitemap_index.xml"):
            url = f"{root.rstrip('/')}{path}"
            if not self._allowed(url):
                continue
            try:
                resp = safe_get(
                    self.session,
                    url,
                    policy=self.url_policy,
                    timeout=15,
                    authorize=lambda candidate: self._crawl_allowed(candidate, root),
                )
                resp.raise_for_status()
                sitemaps.append(url)
            except requests.RequestException:
                continue

        return sitemaps

    # ------------------------------------------------------------------
    # Sitemap parsing
    # ------------------------------------------------------------------

    def _parse_sitemap(
        self, url: str, _depth: int = 0, *, root: str | None = None
    ) -> list[str]:
        """Fetch and parse a sitemap XML document.

        Handles sitemap indexes (``<sitemapindex>`` with nested
        ``<sitemap><loc>``) recursively to a maximum depth of 1.

        Returns a list of page URLs found in the sitemap.
        """
        crawl_root = root or self._get_root(url)
        if not crawl_root or not self._crawl_allowed(url, crawl_root):
            return []
        try:
            resp = safe_get(
                self.session,
                url,
                policy=self.url_policy,
                timeout=30,
                authorize=lambda candidate: self._crawl_allowed(
                    candidate, crawl_root
                ),
            )
            resp.raise_for_status()
        except requests.RequestException:
            return []

        try:
            root = ET.fromstring(resp.content)
        except ET.ParseError:
            return []

        # Strip namespace for tag matching
        root_tag = _strip_ns(root.tag)
        urls: list[str] = []

        if root_tag == "sitemapindex" and _depth < 1:
            # Recursively resolve child sitemaps
            for sitemap_el in root:
                tag = _strip_ns(sitemap_el.tag)
                if tag != "sitemap":
                    continue
                loc_el = None
                for child in sitemap_el:
                    if _strip_ns(child.tag) == "loc":
                        loc_el = child
                        break
                if loc_el is not None and loc_el.text:
                    urls.extend(
                        self._parse_sitemap(
                            loc_el.text.strip(), _depth + 1, root=crawl_root
                        )
                    )
        elif root_tag == "urlset":
            for url_el in root:
                tag = _strip_ns(url_el.tag)
                if tag != "url":
                    continue
                loc_el = None
                for child in url_el:
                    if _strip_ns(child.tag) == "loc":
                        loc_el = child
                        break
                if loc_el is not None and loc_el.text:
                    urls.append(loc_el.text.strip())

        return urls

    # ------------------------------------------------------------------
    # BFS fallback crawl
    # ------------------------------------------------------------------

    def _crawl_from_root(self, root: str, max_pages: int = 100) -> list[str]:
        """BFS crawl from *root* following internal links.

        Uses the shared ``sift.links.extract_links`` helper for link
        extraction from fetched HTML.

        Sleeps 0.5 s between fetches.  Returns a list of discovered URLs.
        """
        root_norm = root.rstrip("/")

        visited: set[str] = set()
        frontier: list[str] = [root_norm]
        discovered: list[str] = []

        while frontier and len(discovered) < max_pages:
            current = frontier.pop(0)
            if current in visited:
                continue
            visited.add(current)

            if not self._allowed(current):
                continue

            try:
                resp = safe_get(
                    self.session,
                    current,
                    policy=self.url_policy,
                    timeout=30,
                    authorize=lambda candidate: self._crawl_allowed(
                        candidate, root_norm
                    ),
                )
                resp.raise_for_status()
            except requests.RequestException:
                continue

            discovered.append(current)

            html = resp.text
            links = extract_links(html, current)

            for link in links:
                # Strip URL fragments to avoid duplicate entries
                clean = link.split("#")[0]
                if clean in visited or clean in frontier:
                    continue
                # Keep only internal links (same root) and robots-allowed URLs.
                if self._is_internal_url(clean, root_norm) and self._allowed(clean):
                    frontier.append(clean)

            if len(discovered) >= max_pages:
                break

            time.sleep(0.5)

        return discovered

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(self, url: str, max_pages: int = 200) -> dict[str, Any]:
        """Main entry point for domain crawling.

        Steps
        -----
        1. Create a DB source with ``kind='crawl'``.
        2. Try sitemap discovery; if found, parse sitemaps for page URLs.
        3. If no sitemap, fall back to BFS crawl from the root.
        4. For each discovered URL, fetch the page, extract content with
           ``trafilatura.extract_with_metadata``, and store via ``db.add_page``.

        Returns
        -------
        dict
            Stats dict with keys ``source_id``, ``urls_discovered``,
            ``pages_fetched``, and ``errors``.
        """
        root = self._get_root(url)
        if not root or max_pages < 1:
            return {
                "source_id": None,
                "urls_discovered": 0,
                "pages_fetched": 0,
                "errors": 1,
            }
        try:
            self.url_policy.validate(root)
        except UnsafeURLError:
            return {
                "source_id": None,
                "urls_discovered": 0,
                "pages_fetched": 0,
                "errors": 1,
            }

        # Create a source record
        source_id = self.db.add_source(
            name=root, feed_url=root, kind="crawl"
        )

        # Phase 1: Sitemap discovery
        sitemaps = self._discover_sitemaps(root)
        urls_to_fetch: list[str] = []

        if sitemaps:
            for sm in sitemaps:
                urls_to_fetch.extend(self._parse_sitemap(sm, root=root))
                if len(urls_to_fetch) >= max_pages:
                    urls_to_fetch = urls_to_fetch[:max_pages]
                    break

        # Sitemaps are untrusted input; keep the crawl scoped to its host.
        urls_to_fetch = list(
            dict.fromkeys(
                page_url
                for page_url in urls_to_fetch
                if self._is_internal_url(page_url, root)
                and self._allowed(page_url)
            )
        )[:max_pages]

        # Phase 2: Fall back to BFS crawl if no sitemap URLs found
        if not urls_to_fetch:
            urls_to_fetch = self._crawl_from_root(root, max_pages=max_pages)

        # Phase 3: Fetch each discovered page
        pages_fetched = 0
        errors = 0

        for page_url in urls_to_fetch:
            if pages_fetched >= max_pages:
                break
            if not self._allowed(page_url):
                continue

            try:
                resp = safe_get(
                    self.session,
                    page_url,
                    policy=self.url_policy,
                    timeout=30,
                    authorize=lambda candidate: self._crawl_allowed(candidate, root),
                )
                resp.raise_for_status()
            except requests.RequestException:
                errors += 1
                continue

            try:
                doc = trafilatura.extract_with_metadata(
                    resp.text,
                    output_format="json",
                    include_links=True,
                )
            except Exception:
                errors += 1
                continue

            if doc is None:
                errors += 1
                continue

            try:
                data: dict[str, Any] = json.loads(doc.text) if doc.text else {}
            except (json.JSONDecodeError, TypeError):
                errors += 1
                continue

            title = data.get("title") or ""
            content = data.get("text") or ""

            if len(content) < 50:
                errors += 1
                continue

            try:
                self.db.add_page(
                    url=page_url,
                    title=title,
                    content=content,
                    source_id=source_id,
                )
                pages_fetched += 1
            except Exception:
                errors += 1

            time.sleep(0.5)

        return {
            "source_id": source_id,
            "urls_discovered": len(urls_to_fetch),
            "pages_fetched": pages_fetched,
            "errors": errors,
            "robots_skipped": sum(self.skipped.values()),
            "robots_skip_reasons": dict(self.skipped),
        }
