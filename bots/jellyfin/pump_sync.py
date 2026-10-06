"""Process and timing helpers the `!watch` pumps share: reaping an ffmpeg, and starting picture and sound together."""

from __future__ import annotations

import asyncio
import contextlib

START_WAIT_SECONDS = 3  # how long one pump holds its first chunk for the other; a title with no audio never sends any


async def reap(process):
    """Kills an ffmpeg and drains its pipe: on Python 3.12 `wait()` never returns while unread output is still queued."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    with contextlib.suppress(ProcessLookupError):
        await process.communicate()


class StartLine:
    """Holds each pump's first chunk until the other has one too, so picture and sound start together.
    Anchoring the picture at launch instead let it race through the transcode's start-up second and run that far ahead."""

    def __init__(self):
        self._video = asyncio.Event()
        self._audio = asyncio.Event()

    async def video_ready(self):
        self._video.set()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._audio.wait(), timeout=START_WAIT_SECONDS)

    async def audio_ready(self):
        self._audio.set()
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._video.wait(), timeout=START_WAIT_SECONDS)

    def video_gone(self):
        self._video.set()

    def audio_gone(self):
        self._audio.set()
