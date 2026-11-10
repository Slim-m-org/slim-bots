"""Copies a direct stream into ffmpeg's stdin, so ffmpeg never opens the url and the public-only policy covers every byte it decodes; see README.md."""

import asyncio
import contextlib
import sys

from slimbots.web import ServiceError, ServiceUnreachable

import music_core

RECONNECTS = 5  # drops in a row that delivered nothing before a live stream is given up on
RECONNECT_DELAY_SECONDS = 2
RAW_AUDIO = {"accept-encoding": "identity"}


class _Dropped(Exception):
    """A live stream was cut mid-body; `carried` is whether it had delivered anything first."""

    def __init__(self, carried):
        super().__init__()
        self.carried = carried


async def _copy(url, stdin):
    """Streams the body at `url` into `stdin`; a live stream (no Content-Length) that is cut raises `_Dropped` so the caller can open it again."""
    carried = False
    async with music_core.stream_service().stream(url, headers=RAW_AUDIO) as answer:
        if not 200 <= answer.status < 300:
            raise ServiceError(f"the stream answered HTTP {answer.status}")
        try:
            async with contextlib.aclosing(answer.chunks()) as chunks:
                async for chunk in chunks:
                    stdin.write(chunk)
                    await stdin.drain()
                    carried = True
        except ServiceUnreachable:
            if "content-length" in answer.headers:
                raise
            raise _Dropped(carried) from None


async def feed(url, stdin):
    """Streams `url` into `stdin` and closes it when the body ends or cannot go on, which ffmpeg reads as the end of the track."""
    barren = 0
    try:
        while True:
            try:
                await _copy(url, stdin)
                return
            except _Dropped as dropped:
                barren = 0 if dropped.carried else barren + 1
                if barren > RECONNECTS:
                    print("music stream feed gave up on a stream that keeps dropping", file=sys.stderr)
                    return
                await asyncio.sleep(RECONNECT_DELAY_SECONDS)
    except (BrokenPipeError, ConnectionResetError):
        pass  # ffmpeg is already gone, from a skip or a stop
    except Exception as err:
        print(f"music stream feed ended: {type(err).__name__}: {err}", file=sys.stderr)
    finally:
        with contextlib.suppress(Exception):
            stdin.close()


def start(url, stdin):
    """The feed as a task; the player cancels it with the ffmpeg it belongs to."""
    return asyncio.create_task(feed(url, stdin), name="music-stream-feed")
