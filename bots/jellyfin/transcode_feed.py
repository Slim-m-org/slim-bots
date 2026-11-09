"""Copies a Jellyfin transcode into ffmpeg's stdin, so the API key rides in this process's request and never in ffmpeg's command line; see README.md."""

from __future__ import annotations

import asyncio
import contextlib
import sys

import httpx

import jellyfin_core

CONNECT_SECONDS = 15
READ_SECONDS = 120  # a transcode silent this long is dead; a paused party stops writing, not reading, so it never waits on this


async def feed(url, stdin):
    """Streams the transcode at `url` into `stdin` and closes it when the body ends or the request fails.
    ffmpeg reads either as the end of its input, so the session sees a dropped stream or a start that never produced a frame."""
    headers = {"Authorization": jellyfin_core.jellyfin_auth_header(), "User-Agent": jellyfin_core.USER_AGENT, "Accept-Encoding": "identity"}
    timeout = httpx.Timeout(READ_SECONDS, connect=CONNECT_SECONDS)
    try:
        async with (
            httpx.AsyncClient(transport=jellyfin_core.TRANSPORT, timeout=timeout, follow_redirects=False) as client,
            client.stream("GET", url, headers=headers) as response,
        ):
            if response.status_code != 200:
                print(f"jellyfin answered the transcode request with {response.status_code}", file=sys.stderr)
                return
            async for chunk in response.aiter_raw():
                stdin.write(chunk)
                await stdin.drain()
    except Exception as err:
        print(f"jellyfin transcode feed ended: {type(err).__name__}: {err}", file=sys.stderr)
    finally:
        with contextlib.suppress(Exception):
            stdin.close()


def start(url, stdin):
    """The feed as a task; the session cancels it with the pipeline it belongs to."""
    return asyncio.create_task(feed(url, stdin), name="jellyfin-transcode-feed")
