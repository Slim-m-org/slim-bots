"""A loopback relay that adds the Jellyfin key to ffmpeg's requests, so the key never sits on ffmpeg's command line; see README.md."""

from __future__ import annotations

import asyncio
import contextlib
import re
import secrets
import sys

import httpx

import music_core

CONNECT_SECONDS = 15
READ_SECONDS = 120
REQUEST_SECONDS = 10
MAX_HEADER_LINES = 64
BYTE_RANGE = re.compile(r"bytes=\d*-\d*")
PASSED_BACK = ("content-type", "content-length", "content-range", "accept-ranges")


class JellyfinRelay:
    """Serves one Jellyfin track at an unguessable loopback url for as long as it plays.
    ffmpeg opens it like the original, Range requests included, so seeking and reconnecting behave as they did when ffmpeg held the key."""

    def __init__(self, upstream):
        self.upstream = upstream
        self.url = ""
        self._path = "/" + secrets.token_urlsafe(24)
        self._server: asyncio.Server | None = None
        self._client: httpx.AsyncClient | None = None
        self._handlers: set[asyncio.Task] = set()

    async def __aenter__(self):
        timeout = httpx.Timeout(READ_SECONDS, connect=CONNECT_SECONDS)
        self._client = httpx.AsyncClient(transport=music_core.TRANSPORT, timeout=timeout, follow_redirects=False)
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.url = f"http://127.0.0.1:{port}{self._path}"
        return self

    async def __aexit__(self, *_exc):
        self._server.close()
        for task in list(self._handlers):
            task.cancel()
        await asyncio.gather(*self._handlers, return_exceptions=True)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._server.wait_closed(), timeout=2)
        await self._client.aclose()

    async def _serve(self, reader, writer):
        task = asyncio.current_task()
        self._handlers.add(task)
        try:
            request = await asyncio.wait_for(_read_request(reader), timeout=REQUEST_SECONDS)
            if request is None:
                await _reply(writer, 400, "Bad Request")
            elif request[0] != "GET" or not secrets.compare_digest(request[1].encode("latin-1"), self._path.encode()):
                await _reply(writer, 404, "Not Found")
            else:
                await self._forward(writer, request[2].get("range", ""))
        except Exception as err:
            print(f"music jellyfin relay ended a request: {type(err).__name__}", file=sys.stderr)
        finally:
            self._handlers.discard(task)
            with contextlib.suppress(Exception):
                writer.close()

    async def _forward(self, writer, wanted_range):
        headers = {"authorization": music_core.jellyfin_auth_header(), "user-agent": music_core.USER_AGENT, "accept-encoding": "identity"}
        if BYTE_RANGE.fullmatch(wanted_range):
            headers["range"] = wanted_range
        async with self._client.stream("GET", self.upstream, headers=headers) as response:
            if 300 <= response.status_code < 400:
                await _reply(writer, 502, "Bad Gateway")
                return
            head = [f"HTTP/1.1 {response.status_code} {response.reason_phrase or 'Status'}"]
            head += [f"{name}: {response.headers[name]}" for name in PASSED_BACK if name in response.headers]
            writer.write(("\r\n".join([*head, "connection: close"]) + "\r\n\r\n").encode("latin-1"))
            async for chunk in response.aiter_raw():
                writer.write(chunk)
                await writer.drain()
            await writer.drain()


async def _read_request(reader):
    """(method, path, lowercased headers) of one HTTP/1.x request head, or None when it is not one."""
    parts = (await reader.readline()).decode("latin-1").split()
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        return None
    headers = {}
    for _ in range(MAX_HEADER_LINES):
        line = (await reader.readline()).decode("latin-1").strip()
        if not line:
            return parts[0], parts[1], headers
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return None


async def _reply(writer, status, reason):
    writer.write(f"HTTP/1.1 {status} {reason}\r\ncontent-length: 0\r\nconnection: close\r\n\r\n".encode("latin-1"))
    await writer.drain()
