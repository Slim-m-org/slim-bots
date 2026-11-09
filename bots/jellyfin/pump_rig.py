"""Test support for the `!watch` pumps: an event loop on a virtual clock, and an audio source that plays in real time like livekit's."""

from __future__ import annotations

import asyncio
import time

from slimbots.testing import FakeVoiceSession


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    """An event loop whose clock jumps straight to the next timer whenever every task is waiting, so seconds of playback take no time."""

    def __init__(self):
        super().__init__()
        self._now = 1000.0
        selector = self._selector
        poll = selector.select

        def jump(timeout=None):
            self._now += timeout or 0.0
            return poll(0)

        selector.select = jump

    def time(self):
        return self._now


class PlayheadAudioSource:
    """Plays what it is given at 1x like livekit's AudioSource: a queue that drains in real time, `capture_frame` waiting while it is full.
    `heard_at(t)` is the second of the content playing at time t, held where a gap in the sound stops it."""

    def __init__(self, now=time.monotonic, queue_ms=100):
        self._now = now
        self._queue_seconds = queue_ms / 1000
        self._plays_until = 0.0
        self._content = 0.0
        self._played = []

    @property
    def queued_duration(self):
        return max(self._plays_until - self._now(), 0.0)

    async def capture_frame(self, frame):
        now = self._now()
        seconds = frame.args[3] / frame.args[1]
        start = max(now, self._plays_until)
        self._played.append((start, start + seconds, self._content))
        self._content += seconds
        self._plays_until = start + seconds
        overflow = self._plays_until - now - self._queue_seconds
        if overflow > 0:
            await asyncio.sleep(overflow)

    def clear_queue(self):
        now = self._now()
        self._played = [(a, min(b, now), c) for a, b, c in self._played if a < now]
        self._plays_until = min(self._plays_until, now)

    def heard_at(self, when):
        heard = 0.0
        for start, end, content in self._played:
            if when < start:
                break
            heard = content + min(when, end) - start
        return heard


class FrameRecorder:
    """A video source that notes when each frame went out; the test writes the frame's number into its first four bytes."""

    def __init__(self, now=time.monotonic):
        self._now = now
        self.sent = []
        self.timestamps_us = []

    def capture_frame(self, frame, timestamp_us=0):
        self.sent.append((self._now(), int.from_bytes(frame.args[3][:4], "big")))
        self.timestamps_us.append(timestamp_us)

    @property
    def frame_count(self):
        return len(self.sent)


class FakeRtc:
    class VideoBufferType:
        I420 = 1

    class VideoFrame:
        def __init__(self, *args):
            self.args = args

    class AudioFrame:
        def __init__(self, *args):
            self.args = args


class PacedVoiceSession(FakeVoiceSession):
    """A fake voice session whose two sources behave like livekit's: sound plays in real time, and the picture notes each frame."""

    def __init__(self, channel_id, now=time.monotonic):
        super().__init__(channel_id)
        self.rtc = FakeRtc
        self._now = now

    async def publish_screen_share(self, **options):
        await super().publish_screen_share(**options)
        return FrameRecorder(self._now), PlayheadAudioSource(self._now, options.get("audio_queue_ms") or 1000)
