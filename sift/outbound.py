"""Shared fail-closed boundary for outbound HTTP(S) requests."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter


Resolver = Callable[[str], Iterable[str]]
Authorizer = Callable[[str], bool]
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_PINNED_DESTINATION: ContextVar[tuple[str, str] | None] = ContextVar(
    "sift_pinned_destination", default=None
)


class UnsafeURLError(requests.RequestException):
    """Raised when a URL could reach a non-public network destination."""


class _PinnedHTTPAdapter(HTTPAdapter):
    """Connect pools to a validated IP while preserving HTTP Host and TLS SNI."""

    def build_connection_pool_key_attributes(self, request, verify, cert=None):
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(
            request, verify, cert
        )
        destination = _PINNED_DESTINATION.get()
        if destination is None:
            return host_params, pool_kwargs
        hostname, address = destination
        host_params["host"] = address
        if host_params["scheme"] == "https":
            pool_kwargs["assert_hostname"] = hostname
            pool_kwargs["server_hostname"] = hostname
        return host_params, pool_kwargs


class PinnedSession(requests.Session):
    """Requests session whose transport never re-resolves validated hostnames."""

    def __init__(self) -> None:
        super().__init__()
        self.trust_env = False
        self.mount("http://", _PinnedHTTPAdapter())
        self.mount("https://", _PinnedHTTPAdapter())

    def get_pinned(
        self, url: str, addresses: tuple[str, ...], **kwargs
    ) -> requests.Response:
        """Try only prevalidated addresses, retaining the original Host/SNI."""
        if kwargs.get("proxies"):
            raise UnsafeURLError("proxies are not supported for pinned requests")
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if hostname is None:
            raise UnsafeURLError("outbound URL has no hostname")
        host_header = f"[{hostname}]" if ":" in hostname else hostname
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        if parsed.port is not None and parsed.port != default_port:
            host_header = f"{host_header}:{parsed.port}"
        headers = dict(kwargs.pop("headers", None) or {})
        headers.setdefault("Host", host_header)
        last_error = None
        for address in addresses:
            token = _PINNED_DESTINATION.set((hostname, address))
            try:
                return super().get(url, headers=headers, **kwargs)
            except requests.ConnectionError as exc:
                last_error = exc
            finally:
                _PINNED_DESTINATION.reset(token)
        if last_error is not None:
            raise last_error
        raise UnsafeURLError("outbound hostname has no validated addresses")


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

    def validate(self, url: str) -> tuple[str, ...]:
        """Return validated connection IPs or raise :class:`UnsafeURLError`."""
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
            addresses = [str(ipaddress.ip_address(host))]
        except ValueError:
            try:
                addresses = list(dict.fromkeys(self.resolver(host)))
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
        return tuple(str(address) for address in parsed_addresses)


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
        addresses = policy.validate(current)
        if authorize is not None and not authorize(current):
            raise UnsafeURLError("outbound URL denied by fetch policy")
        request_kwargs = {
            "timeout": timeout,
            "headers": headers,
            "allow_redirects": False,
        }
        get_pinned = getattr(session, "get_pinned", None)
        if get_pinned is not None:
            response = get_pinned(current, addresses, **request_kwargs)
        elif isinstance(session, requests.Session):
            raise UnsafeURLError("HTTP session does not support pinned DNS")
        else:
            response = session.get(current, **request_kwargs)
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
