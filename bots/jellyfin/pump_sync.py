"""Timing helpers the `!watch` pumps share: reading the audio pipe, starting picture and sound together, and keeping them together."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time

START_WAIT_SECONDS = 3  # how long one pump holds its first chunk for the other; a title with no audio never sends any
SOUND_STALL_SECONDS = 0.5  # how long a frame waits for sound that is not moving before the picture stops following it
SOUND_JOIN_SECONDS = 0.25  # how far behind the picture the sound may start and still pull it into step
WAITING, FOLLOWING, FREE = "waiting", "following", "free"  # a MediaClock's state: no sound yet, on the sound, on the wall
FRAME_WAIT_TICK = 0.02  # longest sleep while a frame waits, so a clock that has stopped moving is looked at again
FRAME_SLACK = 0.001  # a frame this close to due goes now rather than after one more sleep


class AudioPipe:
    """The read end of the ffmpeg's extra audio output as a StreamReader; `stdout` so a pump reads it like a process."""

    def __init__(self, reader, transport):
        self.stdout = reader
        self._transport = transport

    @classmethod
    async def open(cls, read_fd):
        reader = asyncio.StreamReader()
        transport, _protocol = await asyncio.get_running_loop().connect_read_pipe(
            lambda: asyncio.StreamReaderProtocol(reader), os.fdopen(read_fd, "rb", buffering=0),
        )
        return cls(reader, transport)

    def close(self):
        self._transport.close()


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


class MediaClock:
    """The picture's clock: seconds into the title as the sound has played them, or the wall clock when there is no sound to follow.
    The README covers when it follows the sound, waits for it and gives up on it."""

    def __init__(self, source, now=time.monotonic):
        self._source = source
        self._now = now
        self._pushed = 0.0
        self._heard = None
        self._state = WAITING if source is not None else FREE
        self._anchor = None
        self._position = 0.0

    def begin(self):
        """The first frame is ready: counting starts here, however long the transcode took to start."""
        self._anchor = (self._now(), 0.0)

    def push(self, seconds):
        """Call just before handing `seconds` of sound to the source, which counts it from that moment too."""
        self._pushed += seconds

    def sound_ended(self):
        """No more sound is coming: carries on from where the last of it plays out."""
        if self._anchor is not None:
            self._anchor = (self._now(), self.position())
        self._source, self._state = None, FREE

    def give_up(self):
        """The sound is not coming: the picture carries on from where the sound stopped, on the wall clock."""
        if self._state == FOLLOWING:
            self._state, self._anchor = FREE, (self._now(), self._position)

    def resume(self):
        """Left a pause: the clock carries on from where it stopped, not from where the wall clock got to.
        Called by the session, since a pump can sit in a read through the whole pause and never see it."""
        self._anchor = (self._now(), self._position)

    def position(self):
        now = self._now()
        if self._state != FREE and self._pushed > 0:
            self._listen(now)
        self._position = self._heard if self._state == FOLLOWING else self._anchor[1] + now - self._anchor[0]
        return self._position

    def _listen(self, now):
        heard = self._pushed - self._source.queued_duration
        joining = self._state == WAITING and heard != self._heard
        self._heard = heard
        if joining:
            picture = self._anchor[1] + now - self._anchor[0]
            self._state = FOLLOWING if picture - heard <= SOUND_JOIN_SECONDS else FREE


async def wait_for_frame(clock, due, *, stop):
    """True once the clock has reached `due` seconds; False when `stop()` says to leave the frame unsent.
    A frame the sound has already passed is sent, never skipped: skipping freezes the picture when the decoder is the slow part."""
    loop = asyncio.get_running_loop()
    last, since = None, loop.time()
    while not stop():
        position = clock.position()
        lag = position - due
        if lag >= -FRAME_SLACK:
            return True
        now = loop.time()
        if position != last:
            last, since = position, now
        elif now - since > SOUND_STALL_SECONDS:
            clock.give_up()  # the picture may be what blocks the sound, so waiting longer would freeze both
        await asyncio.sleep(min(-lag, FRAME_WAIT_TICK))
    return False
