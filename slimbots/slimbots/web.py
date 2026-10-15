"""`JsonService`: the outbound JSON/HTTP client bots share, so none of them hand-rolls urllib under `asyncio.to_thread`."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from .urlpolicy import Resolver, destination_problem

DEFAULT_TIMEOUT = 20.0
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

    def json(self) -> Any:
        return json.loads(self.body) if self.body.strip() else None


def _origin(url: str) -> tuple[str, str, int | None]:
    parts = urlsplit(url)
    return parts.scheme, (parts.hostname or "").lower(), parts.port or {"http": 80, "https": 443}.get(parts.scheme)


class JsonService:
    """One outside service: a base url, fixed headers, JSON in and out, and a redirect and destination policy.
    Redirects stay on the origin unless `cross_origin_redirects`, so a key header is never replayed elsewhere."""

    def __init__(
        self, name: str, base_url: str | None = None, *, headers: Mapping[str, str] | None = None,
        user_agent: str | None = None, timeout: float = DEFAULT_TIMEOUT, auth_statuses: Iterable[int] = (401, 403),
        max_redirects: int = 5, cross_origin_redirects: bool = False, max_bytes: int | None = None,
        public_only: bool = False, allow_private: bool = False, allow_hosts: Iterable[str] = (),
        transport: httpx.AsyncBaseTransport | None = None, resolver: Resolver | None = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/") if base_url is not None else None
        self.headers = {k.lower(): v for k, v in (headers or {}).items()}
        if user_agent:
            self.headers["user-agent"] = user_agent
        self.timeout = timeout
        self.auth_statuses = tuple(auth_statuses)
        self.max_redirects = max_redirects
        self.cross_origin_redirects = cross_origin_redirects
        self.max_bytes = max_bytes
        self.public_only = public_only
        self.allow_private = allow_private
        self.allow_hosts = tuple(allow_hosts)
        self._transport = transport
        self._resolver = resolver

    def url_for(self, path: str) -> str:
        if path.startswith(("http://", "https://")):
            if self.base_url is not None:
                raise ValueError("an absolute url is only for a service built without a base url")
            return path
        if self.base_url is None:
            raise ValueError("this service has no base url, so every path must be an absolute url")
        return f"{self.base_url}{path}"

    async def _check_destination(self, url: str) -> None:
        if not self.public_only:
            return
        reason = await destination_problem(
            url, allow_private=self.allow_private, allow_hosts=self.allow_hosts, resolver=self._resolver
        )
        if reason is not None:
            raise ServiceBlocked(f"{self.name} host is not publicly routable", reason)

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
        origin = _origin(url)
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport, follow_redirects=False) as client:
            for hop in range(self.max_redirects + 1):
                await self._check_destination(url)
                response = await self._once(client, method, url, merged, content, params if hop == 0 else None)
                target = response.headers.get("location")
                if response.status not in REDIRECT_STATUSES or not target or hop == self.max_redirects:
                    return response
                url = urljoin(url, target)
                if not self.cross_origin_redirects and _origin(url) != origin:
                    raise ServiceError(f"{self.name} redirected to another host", response.status)
                if response.status == 303 or (response.status in (301, 302) and method not in ("GET", "HEAD")):
                    method, content = "GET", None
                    merged.pop("content-type", None)
        raise AssertionError("unreachable")

    async def _once(
        self, client: httpx.AsyncClient, method: str, url: str, headers: dict[str, str], content: bytes | None,
        params: Mapping[str, Any] | None,
    ) -> WebResponse:
        try:
            async with client.stream(method, url, headers=headers, content=content, params=params) as response:
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data += chunk
                    if self.max_bytes is not None and len(data) > self.max_bytes:
                        raise ServiceTooLarge(f"{self.name} answered with more than {self.max_bytes} bytes")
                return WebResponse(response.status_code, {k.lower(): v for k, v in response.headers.items()}, bytes(data))
        except httpx.InvalidURL:
            raise ServiceError(f"{self.name} url is not valid") from None
        except httpx.HTTPError as err:
            raise ServiceUnreachable(f"{self.name} could not be reached ({type(err).__name__})") from None

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
