"""What the bots that decode media with ffmpeg into LiveKit share: the PCM format, the binary, reaping it, and h:mm:ss."""

from __future__ import annotations

import contextlib
import shutil
from typing import Any

PCM_SAMPLE_RATE = 48000
PCM_CHANNELS = 2
PCM_CHUNK_MS = 20
BYTES_PER_SAMPLE = 2


class MediaError(Exception):
    """A media pipeline could not start - a missing ffmpeg, most often."""


def pcm_chunk_samples(chunk_ms: int = PCM_CHUNK_MS, sample_rate: int = PCM_SAMPLE_RATE) -> int:
    return sample_rate * chunk_ms // 1000


def pcm_chunk_bytes(chunk_ms: int = PCM_CHUNK_MS, sample_rate: int = PCM_SAMPLE_RATE, channels: int = PCM_CHANNELS) -> int:
    return pcm_chunk_samples(chunk_ms, sample_rate) * channels * BYTES_PER_SAMPLE


def ffmpeg_binary(error: type[Exception] = MediaError) -> str:
    """The ffmpeg on PATH, or `error` (a bot's own `MediaError` subclass) saying where to install it."""
    path = shutil.which("ffmpeg")
    if path is None:
        raise error("ffmpeg is not on PATH - install it on the bot's host")
    return path


async def reap(process: Any) -> None:
    """Kills an ffmpeg and drains its pipe: on Python 3.12 `wait()` never returns while unread output is still queued."""
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    with contextlib.suppress(ProcessLookupError):
        await process.communicate()


def format_hms(total_seconds: float) -> str:
    """`m:ss`, or `h:mm:ss` from an hour up; a negative count reads as zero."""
    total = max(0, int(total_seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def parse_hms(text: str) -> int:
    """Parses `h:mm:ss`, `mm:ss`, or a bare second count; raises ValueError on anything else."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a valid time: {text!r}")
    numbers = [0] * (3 - len(parts)) + [int(p) for p in parts]
    hours, minutes, seconds = numbers
    return hours * 3600 + minutes * 60 + seconds

