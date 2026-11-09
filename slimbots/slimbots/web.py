"""`JsonService`: the outbound JSON/HTTP client bots share, so none of them hand-rolls urllib under `asyncio.to_thread`."""

from __future__ import annotations

import asyncio
import json
import ssl
from dataclasses import dataclass, field
from typing import Any, Awaitable, Iterable, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from .urlpolicy import Resolver, vet_destination

DEFAULT_TIMEOUT = 20.0
DEFAULT_TOTAL_TIMEOUT = 60.0
DEFAULT_PUBLIC_MAX_BYTES = 10 * 1024 * 1024
MAX_PINNED_ATTEMPTS = 4
REDIRECT_STATUSES = (301, 302, 303, 307, 308)


class ServiceError(Exception):
    """A call to an outside service failed; `str(self)` is safe to show in chat and never carries a key or a url."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class ServiceAuthError(ServiceError):
    """The service rejected the bot's key. Not something to retry."""


class ServiceHTTPError(ServiceError):
    """The service answered with an error status other than an auth rejection."""


class ServiceUnreachable(ServiceError, OSError):
    """No answer: refused, timed out, reset. An `OSError` so existing `except OSError` guards keep catching it."""


class ServiceTooLarge(ServiceError):
    """The body ran past `max_bytes`."""


class ServiceBlocked(ServiceError):
    """The url policy refused the destination; `reason` is `urlpolicy.PRIVATE` or `urlpolicy.UNRESOLVABLE`."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass
class WebResponse:
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    url: str = ""

    def json(self) -> Any:
        return json.loads(self.body) if self.body.strip() else None


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port or {"http": 80, "https": 443}.get(parts.scheme)


def _pinned(url: str, address: str) -> tuple[httpx.URL, dict[str, str], dict[str, str]]:
    """`url` aimed at `address`, with the Host header and TLS server name left as the original host's."""
    original = httpx.URL(url)
    extensions = {"sni_hostname": original.raw_host.decode("ascii")} if original.scheme == "https" else {}
    return original.copy_with(host=address), {"host": original.netloc.decode("ascii")}, extensions


class JsonService:
    """One outside service: a base url, fixed headers, JSON in and out, and a redirect and destination policy.
    Redirects stay on the origin unless `cross_origin_redirects`; `public_only` connects to the addresses it vetted, so a name cannot change its answer between check and connect."""

    def __init__(
        self, name: str, base_url: str | None = None, *, headers: Mapping[str, str] | None = None,
        user_agent: str | None = None, timeout: float = DEFAULT_TIMEOUT, auth_statuses: Iterable[int] = (401, 403),
        max_redirects: int = 5, cross_origin_redirects: bool = False, max_bytes: int | None = None,
        public_only: bool = False, allow_private: bool = False, allow_hosts: Iterable[str] = (),
        transport: httpx.AsyncBaseTransport | None = None, resolver: Resolver | None = None,
        total_timeout: float | None = DEFAULT_TOTAL_TIMEOUT, ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/") if base_url is not None else None
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        if user_agent:
            self.headers["user-agent"] = user_agent
        self.timeout = timeout
        self.total_timeout = total_timeout
        self.auth_statuses = tuple(auth_statuses)
        self.max_redirects = max_redirects
        self.cross_origin_redirects = cross_origin_redirects
        self.max_bytes = DEFAULT_PUBLIC_MAX_BYTES if max_bytes is None and public_only else max_bytes
        self.public_only = public_only
        self.allow_private = allow_private
        self.allow_hosts = tuple(allow_hosts)
        self._transport = transport
        self._resolver = resolver
        self._verify: ssl.SSLContext | bool = ssl_context or True

    def url_for(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            if self.base_url is not None:
                raise ValueError("an absolute url is only for a service built without a base url")
            return path
        if self.base_url is None:
            raise ValueError("this service has no base url, so every path must be an absolute url")
        return f"{self.base_url}{path}"

    async def _check_destination(self, url: str) -> tuple[str, ...]:
        """The addresses to connect to for `url`, empty when the host is not vetted by lookup; `ServiceBlocked` when refused."""
        if not self.public_only:
            return ()
        vetted = await vet_destination(
            url, allow_private=self.allow_private, allow_hosts=self.allow_hosts, resolver=self._resolver
        )
        if vetted.problem is not None:
            raise ServiceBlocked(f"{self.name} host is not publicly routable", vetted.problem)
        return vetted.pins

    async def _bounded(self, call: Awaitable[WebResponse]) -> WebResponse:
        """`call` within `total_timeout`, which spans every redirect hop and the whole body; httpx's own timeout is per phase."""
        if self.total_timeout is None:
            return await call
        try:
            async with asyncio.timeout(self.total_timeout):
                return await call
        except TimeoutError:
            raise ServiceUnreachable(f"{self.name} did not finish answering in {self.total_timeout:g}s") from None

    async def send(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None, body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> WebResponse:
        """One call, redirects handled; any HTTP status is returned as a result, only transport failures raise."""
        url = self.url_for(path)
        merged = {**self.headers, **{k.lower(): v for k, v in (headers or {}).items()}}
        content = json.dumps(body).encode() if body is not None else None
        if content is not None:
            merged.setdefault("content-type", "application/json")
        return await self._bounded(self._follow(method, url, merged, content, params, None))

    async def peek(
        self, path: str, *, size: int = 1024, params: Mapping[str, Any] | None = None, headers: Mapping[str, str] | None = None,
    ) -> WebResponse:
        """A GET followed through its redirects that reads only the first `size` bytes of the final body, so a stream can be inspected.
        `url` on the result is where the redirects ended."""
        merged = {**self.headers, **{k.lower(): v for k, v in (headers or {}).items()}}
        return await self._bounded(self._follow("GET", self.url_for(path), merged, None, params, size))

    async def _follow(
        self, method: str, url: str, headers: dict[str, str], content: bytes | None,
        params: Mapping[str, Any] | None, peek: int | None,
    ) -> WebResponse:
        origin = _origin(url)
        async with httpx.AsyncClient(
            timeout=self.timeout, transport=self._transport, verify=self._verify, follow_redirects=False
        ) as client:
            for hop in range(self.max_redirects + 1):
                pins = await self._check_destination(url)
                response = await self._once(client, method, url, headers, content, params if hop == 0 else None, pins, peek)
                target = response.headers.get("location")
                if response.status not in REDIRECT_STATUSES or not target or hop == self.max_redirects:
                    return response
                url = urljoin(url, target)
                if not self.cross_origin_redirects and _origin(url) != origin:
                    raise ServiceError(f"{self.name} redirected to another host", response.status)
                if response.status == 303 or (response.status in (301, 302) and method not in ("GET", "HEAD")):
                    method, content = "GET", None
                    headers = {k: v for k, v in headers.items() if k != "content-type"}
        raise AssertionError("unreachable")

    async def _once(
        self, client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str], content: bytes | None,
        params: Mapping[str, Any] | None, pins: tuple[str, ...], peek: int | None,
    ) -> WebResponse:
        """One hop, trying each vetted address in turn when a connection cannot be made, so a dead AAAA record still falls back."""
        addresses = pins[:MAX_PINNED_ATTEMPTS] or (None,)
        try:
            for index, address in enumerate(addresses):
                target, extra, extensions = (url, {}, {}) if address is None else _pinned(url, address)
                try:
                    async with client.stream(
                        method, target, headers={**headers, **extra}, content=content, params=params, extensions=extensions
                    ) as response:
                        return await self._read(response, url, peek)
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    if index == len(addresses) - 1:
                        raise
        except httpx.InvalidURL:
            raise ServiceError(f"{self.name} url is not valid") from None
        except httpx.HTTPError as err:
            raise ServiceUnreachable(f"{self.name} could not be reached ({type(err).__name__})") from None
        raise AssertionError("unreachable")

    async def _read(self, response: httpx.Response, url: str, peek: int | None) -> WebResponse:
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data += chunk
            if peek is not None and len(data) >= peek:
                del data[peek:]
                break
            if peek is None and self.max_bytes is not None and len(data) > self.max_bytes:
                raise ServiceTooLarge(f"{self.name} answered with more than {self.max_bytes} bytes")
        headers = {k.lower(): v for k, v in response.headers.items()}
        return WebResponse(response.status_code, headers, bytes(data), url)

    def _raise_for_status(self, response: WebResponse) -> None:
        if response.status in self.auth_statuses:
            raise ServiceAuthError(f"{self.name} api key rejected (HTTP {response.status})", response.status)
        if response.status >= 300:
            raise ServiceHTTPError(f"{self.name} answered HTTP {response.status}", response.status)

    async def request(
        self, method: str, path: str, *, params: Mapping[str, Any] | None = None, body: Any = None,
        headers: Mapping[str, str] | None = None,
    ) -> Any:
        """Parsed JSON (None for an empty body); a rejected key is `ServiceAuthError`, any other error status `ServiceHTTPError`."""
        response = await self.send(method, path, params=params, body=body, headers=headers)
        self._raise_for_status(response)
        try:
            return response.json()
        except ValueError:
            raise ServiceError(f"{self.name} sent a body that is not JSON", response.status) from None

    async def get(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        return await self.request("GET", path, params=params)

    async def post(self, path: str, body: Any = None, params: Mapping[str, Any] | None = None) -> Any:
        return await self.request("POST", path, params=params, body=body)

    async def get_bytes(self, path: str, params: Mapping[str, Any] | None = None) -> bytes:
        """The raw body of a successful GET, for an image or a download."""
        response = await self.send("GET", path, params=params)
        self._raise_for_status(response)
        return response.body
