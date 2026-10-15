"""The one URL policy: which configured service URLs may carry a key, and which hosts a bot may be pointed at."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Awaitable, Callable, Iterable
from urllib.parse import urlsplit

UNRESOLVABLE = "unresolvable"
PRIVATE = "private"

Resolver = Callable[[str], Awaitable[Iterable[str]]]


def _address(text: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address(text.split("%", 1)[0])
    except ValueError:
        return None
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def is_loopback_host(host: str) -> bool:
    """`localhost` or a loopback address; the one place a key may cross the wire unencrypted without an opt-in."""
    ip = _address(host)
    return host.lower() == "localhost" or (ip is not None and ip.is_loopback)


def _embedded_v4(ip: ipaddress.IPv6Address) -> list[ipaddress.IPv4Address]:
    """The IPv4 addresses an IPv6 one can carry: 6to4, Teredo's client and the NAT64 well-known prefix."""
    found = []
    if ip.sixtofour is not None:
        found.append(ip.sixtofour)
    if ip.teredo is not None:
        found.append(ip.teredo[1])
    if ip in ipaddress.IPv6Network("64:ff9b::/96"):
        found.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return found


def is_public_address(address: str) -> bool:
    """True only for a globally routable address, so private, loopback, link-local, CGNAT, multicast and reserved all fail.
    The checks are listed out instead of trusting `is_global` alone, which differs between Python versions for multicast."""
    ip = _address(address)
    if ip is None:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and not all(is_public_address(str(v4)) for v4 in _embedded_v4(ip)):
        return False
    flagged = (ip.is_private, ip.is_loopback, ip.is_link_local, ip.is_multicast, ip.is_reserved, ip.is_unspecified)
    return ip.is_global and not any(flagged)


def service_url_problem(
    setting: str, url: str, *, allow_http: bool = False, bare_hostnames: bool = False, loopback_http: bool = True,
    opt_in: str | None = None, key_setting: str | None = None,
) -> str | None:
    """Why `url` cannot be a keyed service address, or None.
    http is accepted for loopback (unless `loopback_http` is off), for a dotless docker service name when `bare_hostnames`, and anywhere when the operator set `allow_http`."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in ("https", "http") or not host:
        return f"{setting} must be an http(s) URL with a host"
    if parts.username or parts.password:
        return f"{setting} must not embed credentials; the key goes in {key_setting or 'its own setting'}"
    if parts.scheme == "https" or allow_http or (loopback_http and is_loopback_host(host)):
        return None
    if bare_hostnames and "." not in host and _address(host) is None:
        return None
    hint = f"; use https, or set {opt_in}=1 for a trusted LAN host" if opt_in else ": a key on the wire is a leak"
    where = " to a non-loopback host" if loopback_http else ""
    return f"refusing a plaintext {setting}{where}{hint}"


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


async def destination_problem(
    url: str, *, allow_private: bool = False, allow_hosts: Iterable[str] = (), resolver: Resolver | None = None
) -> str | None:
    """`UNRESOLVABLE` or `PRIVATE` when `url` is not an http(s) URL to a public host, else None.
    `allow_private` and `allow_hosts` are the operator's opt-ins; every resolved answer must be public, not just the first."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return UNRESOLVABLE
    if allow_private or host in {h.lower() for h in allow_hosts}:
        return None
    try:
        addresses = [host] if _address(host) is not None else list(await (resolver or _resolve)(host))
    except OSError:
        return UNRESOLVABLE
    if not addresses:
        return UNRESOLVABLE
    return None if all(is_public_address(a) for a in addresses) else PRIVATE


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()

