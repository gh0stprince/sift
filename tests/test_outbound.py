"""Deterministic outbound URL and redirect boundary tests."""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest
import requests

from sift.outbound import OutboundPolicy, UnsafeURLError, safe_get
from sift.crawler import DomainCrawler
from sift.feeds import FeedFetcher
from sift.pulse import PulseEngine
from sift.robots import RobotsPolicy


@dataclass
class FakeResponse:
    """Minimal requests response for redirect tests."""

    status_code: int = 200
    headers: dict[str, str] = field(default_factory=dict)
    text: str = "ok"
    closed: bool = False

    def close(self) -> None:
        self.closed = True

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(response=self)


class FakeSession:
    """Return configured responses while recording redirect flags."""

    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, bool]] = []
        self.headers: dict[str, str] = {}

    def get(self, url: str, **kwargs) -> FakeResponse:
        self.calls.append((url, kwargs["allow_redirects"]))
        return next(self.responses)

    def close(self) -> None:
        """Match requests.Session cleanup."""


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://127.0.0.1/admin",
        "http://10.0.0.1/",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://224.0.0.1/",
        "http://user:password@example.com/",
    ],
)
def test_policy_rejects_non_public_destinations(url: str) -> None:
    policy = OutboundPolicy(resolver=lambda _host: ["93.184.216.34"])

    with pytest.raises(UnsafeURLError):
        policy.validate(url)


def test_policy_rejects_hostname_if_any_dns_answer_is_private() -> None:
    policy = OutboundPolicy(
        resolver=lambda _host: ["93.184.216.34", "192.168.1.10"]
    )

    with pytest.raises(UnsafeURLError, match="non-public"):
        policy.validate("https://example.com/page")


def test_safe_get_blocks_private_redirect_before_second_request() -> None:
    redirect = FakeResponse(302, {"Location": "http://127.0.0.1/private"})
    session = FakeSession([redirect])
    policy = OutboundPolicy(resolver=lambda _host: ["93.184.216.34"])

    with pytest.raises(UnsafeURLError):
        safe_get(session, "https://example.com/start", policy=policy, timeout=5)

    assert session.calls == [("https://example.com/start", False)]
    assert redirect.closed


def test_safe_get_validates_and_authorizes_every_public_redirect() -> None:
    redirect = FakeResponse(301, {"Location": "https://other.example/final"})
    final = FakeResponse()
    session = FakeSession([redirect, final])
    resolved = {
        "example.com": ["93.184.216.34"],
        "other.example": ["93.184.216.35"],
    }
    policy = OutboundPolicy(resolver=lambda host: resolved[host])
    authorized: list[str] = []

    response = safe_get(
        session,
        "https://example.com/start",
        policy=policy,
        timeout=5,
        authorize=lambda url: authorized.append(url) or True,
    )

    assert response is final
    assert authorized == [
        "https://example.com/start",
        "https://other.example/final",
    ]
    assert session.calls == [
        ("https://example.com/start", False),
        ("https://other.example/final", False),
    ]


def test_robots_fetch_uses_boundary_and_disables_redirects() -> None:
    response = FakeResponse(text="User-agent: *\nAllow: /\n")
    session = FakeSession([response])
    policy = RobotsPolicy(
        session,
        "Sift-Test/0.1",
        url_policy=OutboundPolicy(resolver=lambda _host: ["93.184.216.34"]),
    )

    assert policy.allowed("https://example.com/page")
    assert session.calls == [("https://example.com/robots.txt", False)]


def test_feed_redirect_cannot_reach_private_service() -> None:
    robots = FakeResponse(text="User-agent: *\nAllow: /\n")
    redirect = FakeResponse(302, {"Location": "http://127.0.0.1/feed"})
    session = FakeSession([robots, redirect])
    fetcher = FeedFetcher(
        db_instance=None,
        session=session,
        resolver=lambda _host: ["93.184.216.34"],
    )

    with pytest.raises(UnsafeURLError):
        fetcher.fetch_feed("https://example.com/feed")

    assert session.calls == [
        ("https://example.com/robots.txt", False),
        ("https://example.com/feed", False),
    ]


def test_pulse_rejects_private_search_result_without_network_call() -> None:
    session = FakeSession([])
    engine = PulseEngine(
        db=None,
        session=session,
        resolver=lambda _host: ["93.184.216.34"],
    )

    assert engine._fetch_and_store("http://127.0.0.1/private", pulse_id=1) is None
    assert not session.calls


class _SourceDB:
    def __init__(self) -> None:
        self.sources = []

    def add_source(self, **kwargs):
        self.sources.append(kwargs)
        return 1


def test_crawler_rejects_private_root_before_creating_source() -> None:
    database = _SourceDB()
    session = FakeSession([])
    crawler = DomainCrawler(
        database,
        session=session,
        resolver=lambda _host: ["93.184.216.34"],
    )

    result = crawler.run("http://10.0.0.1/admin", max_pages=1)

    assert result["errors"] == 1
    assert not database.sources
    assert not session.calls
