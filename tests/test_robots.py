"""Deterministic tests for conservative robots.txt policy handling."""

from __future__ import annotations

from dataclasses import dataclass

import requests

from sift.robots import RobotsPolicy
from sift.outbound import OutboundPolicy


def public_policy() -> OutboundPolicy:
    """Resolve deterministic test hosts to a documentation-only public IP."""
    return OutboundPolicy(resolver=lambda _host: ["93.184.216.34"])


@dataclass
class FakeResponse:
    """Small requests-response stand-in for policy tests."""

    text: str
    status_code: int = 200

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeSession:
    """Requests-session stand-in that returns one configured response."""

    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs: object) -> FakeResponse:
        self.calls.append(url)
        return self.response


def test_allow_overrides_disallow_and_wildcards() -> None:
    session = FakeSession(FakeResponse(
        "User-agent: Sift\nDisallow: /private\nAllow: /private/public\n"
        "Disallow: /*.pdf$\n"
    ))
    policy = RobotsPolicy(
        session, "Sift/0.1", refresh_seconds=60, url_policy=public_policy()
    )

    assert policy.allowed("https://example.test/private/public")
    assert not policy.allowed("https://example.test/private/secret?x=1")
    assert not policy.allowed("https://example.test/files/report.pdf")
    assert policy.allowed("https://example.test/files/report.pdf?download=1")


def test_policy_is_cached_until_bounded_refresh() -> None:
    now = [0.0]
    session = FakeSession(FakeResponse("User-agent: *\nDisallow: /nope\n"))
    policy = RobotsPolicy(
        session,
        "Sift/0.1",
        refresh_seconds=10,
        clock=lambda: now[0],
        url_policy=public_policy(),
    )

    assert not policy.allowed("https://example.test/nope")
    assert not policy.allowed("https://example.test/nope/again")
    assert session.calls == ["https://example.test/robots.txt"]
    now[0] = 11
    assert not policy.allowed("https://example.test/nope")
    assert session.calls == [
        "https://example.test/robots.txt",
        "https://example.test/robots.txt",
    ]


def test_unavailable_and_malformed_robots_fail_closed() -> None:
    for response in (FakeResponse("", 503), FakeResponse("not robots syntax")):
        policy = RobotsPolicy(
            FakeSession(response), "Sift/0.1", url_policy=public_policy()
        )
        decision = policy.check("https://example.test/anything")
        assert not decision.allowed
        assert decision.reason == "robots_unavailable"


def test_sitemap_directives_are_cached_and_exposed_without_content_logging() -> None:
    session = FakeSession(FakeResponse(
        "User-agent: *\nAllow: /\nSitemap: https://example.test/map.xml\n"
    ))
    policy = RobotsPolicy(session, "Sift/0.1", url_policy=public_policy())

    assert policy.sitemaps("https://example.test") == ("https://example.test/map.xml",)
    assert policy.cache_size() == 1
    assert session.calls == ["https://example.test/robots.txt"]
