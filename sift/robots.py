"""Conservative, cached robots.txt enforcement for Sift web fetches."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse, urlsplit

import requests

from sift.outbound import OutboundPolicy, UnsafeURLError, safe_get


@dataclass(frozen=True)
class RobotsDecision:
    """The result of checking one URL against its origin's robots policy."""

    allowed: bool
    reason: str


@dataclass
class _CachedPolicy:
    rules: tuple[tuple[str, bool], ...] | None
    fetched_at: float
    available: bool
    sitemaps: tuple[str, ...] = ()


class RobotsPolicy:
    """Fetch and cache robots.txt policies, failing closed on unavailable rules.

    A successful 2xx robots response is cached for ``refresh_seconds``. Any
    non-2xx response, timeout, transport error, or unusable response denies
    URLs on that origin for the same bounded period. This prevents a temporary
    robots outage from silently bypassing a site's exclusions.
    """

    def __init__(
        self,
        session: requests.Session,
        user_agent: str,
        refresh_seconds: float = 3600.0,
        clock=time.monotonic,
        url_policy: OutboundPolicy | None = None,
    ) -> None:
        self.session = session
        self.user_agent = user_agent
        self.agent_token = user_agent.split("/", 1)[0].strip() or user_agent
        self.refresh_seconds = refresh_seconds
        self._clock = clock
        self._cache: dict[str, _CachedPolicy] = {}
        self.url_policy = url_policy or OutboundPolicy()

    @staticmethod
    def origin(url: str) -> str:
        """Return the scheme/host/port origin for *url*, or an empty string."""
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ""
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        port = f":{parsed.port}" if parsed.port else ""
        return f"{parsed.scheme}://{host}{port}"

    def _load(self, origin: str) -> _CachedPolicy:
        now = self._clock()
        cached = self._cache.get(origin)
        if cached is not None and now - cached.fetched_at < self.refresh_seconds:
            return cached

        try:
            response = safe_get(
                self.session,
                f"{origin}/robots.txt",
                policy=self.url_policy,
                timeout=15,
                headers={"User-Agent": self.user_agent},
            )
            response.raise_for_status()
            lines = response.text.splitlines()
            meaningful = [
                line for line in lines
                if line.split("#", 1)[0].strip()
            ]
            known = (
                "user-agent:", "disallow:", "allow:", "sitemap:",
                "crawl-delay:", "request-rate:", "visit-time:", "host:",
            )
            if meaningful and not any(
                line.strip().lower().startswith(known) for line in meaningful
            ):
                raise ValueError("robots.txt has no recognized directives")
            groups: list[tuple[list[str], list[tuple[str, bool]]]] = []
            agents: list[str] = []
            rules: list[tuple[str, bool]] = []
            for raw_line in lines + ["User-agent:"]:
                line = raw_line.split("#", 1)[0].strip()
                if not line or ":" not in line:
                    continue
                directive, value = line.split(":", 1)
                directive, value = directive.strip().lower(), value.strip()
                if directive == "user-agent":
                    if agents and rules:
                        groups.append((agents, rules))
                        agents, rules = [], []
                    if value:
                        agents.append(value.lower())
                elif directive in {"allow", "disallow"} and agents and value:
                    # Empty Disallow means allow everything.
                    rules.append((value, directive == "allow"))
            if meaningful and not groups:
                raise ValueError("robots.txt has no usable groups")
            selected = self._select_rules(groups)
            sitemaps = tuple(
                line.split(":", 1)[1].strip()
                for line in lines
                if line.strip().lower().startswith("sitemap:")
                and line.split(":", 1)[1].strip()
            )
            loaded = _CachedPolicy(tuple(selected), now, True, sitemaps)
        except (requests.RequestException, ValueError, UnicodeError):
            loaded = _CachedPolicy(None, now, False)

        self._cache[origin] = loaded
        return loaded

    def _select_rules(
        self, groups: list[tuple[list[str], list[tuple[str, bool]]]]
    ) -> list[tuple[str, bool]]:
        """Choose the configured agent's group, falling back to ``*``."""
        token = self.agent_token.lower()
        exact: list[tuple[str, bool]] = []
        wildcard: list[tuple[str, bool]] = []
        found_exact = False
        for agents, rules in groups:
            if token in agents:
                found_exact = True
                exact.extend(rules)
            elif "*" in agents:
                wildcard.extend(rules)
        return exact if found_exact else wildcard

    @staticmethod
    def _can_fetch(rules: tuple[tuple[str, bool], ...], url: str) -> bool:
        """Apply longest-match rules; Allow wins when match lengths tie."""
        parsed = urlsplit(url)
        target = parsed.path or "/"
        if parsed.query:
            target += "?" + parsed.query
        winner_specificity: int | None = None
        winner_allowed = True
        for pattern, allowed in rules:
            regex = re.escape(pattern)
            if pattern.endswith("$"):
                regex = regex[:-2] + r"$"
            else:
                regex += ".*"
            regex = regex.replace(r"\*", ".*")
            if re.match("^" + regex, target) is None:
                continue
            specificity = len(pattern.replace("*", "").rstrip("$"))
            if (
                winner_specificity is None
                or specificity > winner_specificity
                or (specificity == winner_specificity and allowed)
            ):
                winner_specificity = specificity
                winner_allowed = allowed
        return winner_specificity is None or winner_allowed

    def check(self, url: str) -> RobotsDecision:
        """Return whether Sift may fetch *url* and a non-sensitive reason."""
        try:
            self.url_policy.validate(url)
        except UnsafeURLError:
            return RobotsDecision(False, "unsafe_address")
        origin = self.origin(url)
        if not origin:
            return RobotsDecision(False, "invalid_origin")
        policy = self._load(origin)
        if not policy.available or policy.rules is None:
            return RobotsDecision(False, "robots_unavailable")
        if self._can_fetch(policy.rules, url):
            return RobotsDecision(True, "allowed")
        return RobotsDecision(False, "robots_disallowed")

    def allowed(self, url: str) -> bool:
        """Compatibility shortcut for callers needing only a boolean."""
        return self.check(url).allowed

    def sitemaps(self, origin: str) -> tuple[str, ...]:
        """Return sitemap directives from the cached robots response."""
        return self._load(origin).sitemaps

    def cache_size(self) -> int:
        """Return the number of cached origins (useful for diagnostics/tests)."""
        return len(self._cache)
