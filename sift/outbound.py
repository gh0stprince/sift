"""Shared fail-closed boundary for outbound HTTP(S) requests."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import requests


Resolver = Callable[[str], Iterable[str]]
Authorizer = Callable[[str], bool]
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


class UnsafeURLError(requests.RequestException):
    """Raised when a URL could reach a non-public network destination."""


def _resolve_host(host: str) -> set[str]:
    """Resolve all stream addresses for *host*."""
    return {
        item[4][0]
        for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    }


class OutboundPolicy:
    """Validate that outbound URLs resolve exclusively to public IP addresses."""

    def __init__(self, resolver: Resolver | None = None) -> None:
        self.resolver = resolver or _resolve_host

    def validate(self, url: str) -> str:
        """Return *url* when safe, otherwise raise :class:`UnsafeURLError`."""
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise UnsafeURLError("invalid outbound URL") from exc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            raise UnsafeURLError("outbound URL must use HTTP(S)")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeURLError("outbound URL credentials are not allowed")
        if port is not None and not 1 <= port <= 65535:
            raise UnsafeURLError("invalid outbound URL port")

        host = parsed.hostname
        try:
            addresses = {str(ipaddress.ip_address(host))}
        except ValueError:
            try:
                addresses = set(self.resolver(host))
            except (OSError, ValueError, TypeError) as exc:
                raise UnsafeURLError("outbound hostname could not be resolved") from exc
        if not addresses:
            raise UnsafeURLError("outbound hostname has no resolved addresses")

        try:
            parsed_addresses = [ipaddress.ip_address(address) for address in addresses]
        except ValueError as exc:
            raise UnsafeURLError("outbound hostname returned an invalid address") from exc
        if any(
            not address.is_global
            or address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_multicast
            or address.is_reserved
            or address.is_unspecified
            for address in parsed_addresses
        ):
            raise UnsafeURLError("outbound hostname resolves to a non-public address")
        return url


def safe_get(
    session,
    url: str,
    *,
    policy: OutboundPolicy,
    timeout: float,
    headers: dict[str, str] | None = None,
    authorize: Authorizer | None = None,
    max_redirects: int = 5,
):
    """GET a URL while validating and authorizing every redirect hop."""
    current = url
    for _hop in range(max_redirects + 1):
        policy.validate(current)
        if authorize is not None and not authorize(current):
            raise UnsafeURLError("outbound URL denied by fetch policy")
        response = session.get(
            current,
            timeout=timeout,
            headers=headers,
            allow_redirects=False,
        )
        status_code = getattr(response, "status_code", 200)
        response_headers = getattr(response, "headers", {})
        location = response_headers.get("Location") if status_code in _REDIRECT_STATUSES else None
        if not location:
            return response
        next_url = urljoin(current, location)
        close = getattr(response, "close", None)
        if close is not None:
            close()
        current = next_url
    raise requests.TooManyRedirects(f"more than {max_redirects} redirects")
