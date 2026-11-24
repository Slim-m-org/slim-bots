"""The one URL policy: which configured service URLs may carry a key, and which hosts a bot may be pointed at."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import Awaitable, Callable, Iterable, NamedTuple
from urllib.parse import urlsplit

UNRESOLVABLE = "unresolvable"
PRIVATE = "private"

Resolver = Callable[[str], Awaitable[Iterable[str]]]
SyncResolver = Callable[[str], Iterable[str]]
DOCKER_BRIDGE = ipaddress.IPv4Network("172.16.0.0/12")
IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


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


def resolve_addresses(host: str) -> list[str]:
    """Every address `host` resolves to, blocking; the startup checks run before the event loop."""
    return [str(info[4][0]) for info in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)]


def _resolves_only_to(host: str, accept: Callable[[IPAddress], bool], resolve: SyncResolver | None) -> bool:
    literal = _address(host)
    if literal is not None:
        return accept(literal)
    try:
        answers = [_address(a) for a in (resolve or resolve_addresses)(host)]
    except (OSError, UnicodeError):
        return False
    return bool(answers) and all(ip is not None and accept(ip) for ip in answers)


def _is_lan(ip: IPAddress) -> bool:
    return not is_public_address(str(ip))


def _is_docker_bridge(ip: IPAddress) -> bool:
    return ip.is_loopback or (isinstance(ip, ipaddress.IPv4Address) and ip in DOCKER_BRIDGE)


def service_url_problem(
    setting: str, url: str, *, allow_http: bool = False, bare_hostnames: bool = False, loopback_http: bool = True,
    opt_in: str | None = None, key_setting: str | None = None, resolve: SyncResolver | None = None,
) -> str | None:
    """Why `url` cannot be a keyed service address, or None.
    http is accepted for loopback (unless `loopback_http` is off), for a dotless docker service name when `bare_hostnames` and
    it resolves only to loopback or Docker's bridge range, and for a host whose every address is private when the operator set `allow_http`."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.scheme not in ("https", "http") or not host:
        return f"{setting} must be an http(s) URL with a host"
    if parts.username or parts.password:
        return f"{setting} must not embed credentials; the key goes in {key_setting or 'its own setting'}"
    if parts.scheme == "https" or (loopback_http and is_loopback_host(host)):
        return None
    if allow_http:
        if _resolves_only_to(host, _is_lan, resolve):
            return None
        return f"refusing a plaintext {setting}: {opt_in or 'the http opt-in'} covers only a host on your own network, and {host} is not one; use https"
    bare = bare_hostnames and "." not in host and _address(host) is None
    if bare and _resolves_only_to(host, _is_docker_bridge, resolve):
        return None
    advice = f"use https, or set {opt_in}=1 for a trusted LAN host" if opt_in else "a key on the wire is a leak"
    if bare:
        return f"refusing a plaintext {setting}: {host} does not resolve to loopback or a Docker bridge address; {advice}"
    where = " to a non-loopback host" if loopback_http else ""
    return f"refusing a plaintext {setting}{where}{'; ' if opt_in else ': '}{advice}"


async def _resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class Destination(NamedTuple):
    """`problem` is `UNRESOLVABLE`, `PRIVATE` or None; `pins` are the public addresses a lookup vouched for, to connect to instead of resolving again."""

    problem: str | None
    pins: tuple[str, ...] = ()


async def vet_destination(
    url: str, *, allow_private: bool = False, allow_hosts: Iterable[str] = (), resolver: Resolver | None = None
) -> Destination:
    """Whether `url` is an http(s) URL to a public host, and the addresses to connect to, IPv4 first so a host without IPv6 never waits on one.
    `allow_private` and `allow_hosts` are the operator's opt-ins; every resolved answer must be public, not just the first."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not host:
        return Destination(UNRESOLVABLE)
    if allow_private or host in {h.lower() for h in allow_hosts}:
        return Destination(None)
    if _address(host) is not None:
        return Destination(None if is_public_address(host) else PRIVATE)
    try:
        addresses = list(await (resolver or _resolve)(host))
    except OSError:
        return Destination(UNRESOLVABLE)
    if not addresses:
        return Destination(UNRESOLVABLE)
    if not all(is_public_address(a) for a in addresses):
        return Destination(PRIVATE)
    distinct = dict.fromkeys(str(_address(a)) for a in addresses)
    return Destination(None, tuple(sorted(distinct, key=lambda a: ":" in a)))


async def destination_problem(
    url: str, *, allow_private: bool = False, allow_hosts: Iterable[str] = (), resolver: Resolver | None = None
) -> str | None:
    """`UNRESOLVABLE` or `PRIVATE` when `url` is not an http(s) URL to a public host, else None."""
    return (await vet_destination(url, allow_private=allow_private, allow_hosts=allow_hosts, resolver=resolver)).problem


def host_of(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()

