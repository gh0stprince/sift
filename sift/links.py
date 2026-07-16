"""Shared HTML link-extraction helpers for Pulse and Crawler."""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urljoin


class LinkExtractor(HTMLParser):
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


def extract_links(html: str, base_url: str) -> list[str]:
    """Parse HTML and return absolute http/s URLs, excluding social media."""
    parser = LinkExtractor()
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
        if parsed in SOCIAL_DOMAINS:
            continue
        out.append(absolute)

    return out
