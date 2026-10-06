"""Named stream-quality presets for `!quality`; the resolution/bitrate pairs are what README.md's cost table measured."""

from __future__ import annotations

import math
from dataclasses import dataclass
from fractions import Fraction

import jellyfin_core

HEAVY_WIDTH = 1920
VP8_EIGHT_THREAD_AREA = 1920 * 1080
FULL_HD_CLASS_MAX_HEIGHT = 1088


def below_vp8_thread_jump(width, height):
    """Trims a 1080p-class height to stay under libwebrtc's 8-thread VP8 cutoff, which stalls on a busy host; see README.md."""
    if width * height < VP8_EIGHT_THREAD_AREA or height > FULL_HD_CLASS_MAX_HEIGHT:
        return width, height
    return width, (VP8_EIGHT_THREAD_AREA - 1) // width // 8 * 8


def playback_fps(item, ceiling):
    """The title's own frame rate, or the largest whole fraction of it under `ceiling`, so no frame is shown twice.
    A 23.976 fps film paced at 30 repeated every fourth frame; `ceiling` when Jellyfin gives no rate."""
    rate = next((s.get("RealFrameRate") or s.get("AverageFrameRate") for s in item.get("MediaStreams") or [] if s.get("Type") == "Video"), None)
    if not rate or rate <= 0:
        return Fraction(ceiling)
    divided = Fraction(rate).limit_denominator(1001) / max(1, math.ceil(rate / ceiling - 1e-6))
    return divided.limit_denominator(1001)


@dataclass(frozen=True)
class Quality:
    name: str
    width: int
    height: int
    video_bitrate: int
    webrtc_bitrate: int | None = None

    @property
    def publish_bitrate(self):
        return self.webrtc_bitrate or self.video_bitrate

    def describe(self):
        return f"{self.name} ({self.width}x{self.height}, up to {self.video_bitrate // 1000} kbps)"

    @property
    def is_heavy(self):
        return self.width >= HEAVY_WIDTH

    @property
    def frame_size(self):
        """The size actually decoded and published, which differs from the preset's only for a 1080p-class one."""
        return below_vp8_thread_jump(self.width, self.height)


PRESETS = {
    "low": Quality("low", 854, 480, 1_500_000),
    "medium": Quality("medium", 1280, 720, 4_000_000),
    "high": Quality("high", 1920, 1080, 8_000_000),
}

HEAVY_WARNING = "1080p costs the bot's host about twice the CPU of 720p - see the README's Stream quality section."


def configured_default():
    """What the operator's `JELLYFIN_STREAM_*` env settings ask for, so a stream that never sees `!quality` is unchanged."""
    return Quality(
        "default", jellyfin_core.JELLYFIN_STREAM_WIDTH, jellyfin_core.JELLYFIN_STREAM_HEIGHT,
        jellyfin_core.JELLYFIN_STREAM_MAX_BITRATE, jellyfin_core.JELLYFIN_STREAM_WEBRTC_MAX_BITRATE,
    )


def find_preset(name):
    return PRESETS.get((name or "").strip().lower())


def preset_names():
    return ", ".join(f"`{name}`" for name in PRESETS)
