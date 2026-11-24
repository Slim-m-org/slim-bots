"""One watch party per voice call: the queue, the room's clock, and the server's watch session that every viewer follows (slim-m decision 0050).
The bot plays nothing itself; each viewer's app plays the video in YouTube's own embed and holds it on this clock."""

from __future__ import annotations

import asyncio
import logging
import time

from slimbots import ApiError
from slimbots.hidden_chars import is_hidden_char
from slimbots.voice import watch_empty

log = logging.getLogger("youtube.party")

TICK_SECONDS = 5.0
# The server refills one write per 2 seconds after a burst of 4, so a mashed control must not outrun it.
MIN_GAP_SECONDS = 2.0
MAX_TITLE_CHARS = 200
MAX_QUEUE = 50
ROSTER_POLL_SECONDS = 5.0
PROVIDER = "youtube"
MAX_POSITION_SECONDS = 7 * 24 * 3600

_parties = {}


def wire_title(title):
    """What the server accepts: no hidden characters, trimmed, at most 200 characters, never blank."""
    visible = "".join(char for char in title if not is_hidden_char(char)).strip()
    return visible[:MAX_TITLE_CHARS].strip() or "YouTube video"


def party_in(channel_id):
    party = _parties.get(channel_id)
    if party is not None and party.finished:
        del _parties[channel_id]
        return None
    return party


def live_parties():
    for channel_id in [cid for cid, p in _parties.items() if p.finished]:
        del _parties[channel_id]
    return dict(_parties)


def clear():
    _parties.clear()


class Party:
    """The room's video, where it is, and what plays next; every change is stated to the server at once."""

    def __init__(self, bot, voice_channel_id, voice_session, started_by_id, *, on_end=None, on_next=None, clock=time.monotonic):
        self.bot = bot
        self.voice_channel_id = voice_channel_id
        self.voice_session = voice_session
        self.started_by_id = started_by_id
        self.clock = clock
        self.queue = []
        self.current = None
        self.paused = False
        self.finished = False
        self.lock = asyncio.Lock()
        self._anchor_seconds = 0.0
        self._anchor_at = clock()
        self._on_end = on_end
        self._on_next = on_next
        self._wake_sync = asyncio.Event()
        self._wake_monitor = asyncio.Event()
        self._dirty = True
        self._seeked = False
        self._gave_up = False
        self._controller = started_by_id
        self._tasks = []

    @property
    def position_seconds(self):
        elapsed = 0.0 if self.paused else self.clock() - self._anchor_at
        position = self._anchor_seconds + max(0.0, elapsed)
        duration = self.current.duration_seconds if self.current else None
        return min(position, float(duration)) if duration else position

    def start(self, video):
        _parties[self.voice_channel_id] = self
        self._tasks = [
            self.bot.background(self._sync_loop(), name=f"youtube-sync-{self.voice_channel_id}"),
            self.bot.background(self._monitor(), name=f"youtube-monitor-{self.voice_channel_id}"),
        ]
        self.play(video)

    def play(self, video):
        """Starts `video` where its link asked, or at the beginning."""
        self.current = video
        self.paused = False
        self._move(video.start_seconds)

    def _move(self, seconds):
        self._anchor_seconds = min(max(0.0, float(seconds)), float(MAX_POSITION_SECONDS))
        self._anchor_at = self.clock()
        self._changed(seeked=True)

    def pause(self):
        if self.paused:
            return False
        self._anchor_seconds = self.position_seconds
        self.paused = True
        self._changed()
        return True

    def resume(self):
        if not self.paused:
            return False
        self._anchor_at = self.clock()
        self.paused = False
        self._changed()
        return True

    def seek(self, seconds):
        duration = self.current.duration_seconds if self.current else None
        self._move(min(seconds, duration) if duration else seconds)

    def enqueue(self, video):
        """Adds `video` and returns its place in line, or None once the queue is full."""
        if len(self.queue) >= MAX_QUEUE:
            return None
        self.queue.append(video)
        return len(self.queue)

    async def skip(self):
        """Plays the next video, or ends the party when nothing is queued; returns what plays now, or None."""
        if not self.queue:
            await self.stop(reason="the queue is empty")
            return None
        self.play(self.queue.pop(0))
        return self.current

    def wake_monitor(self):
        self._wake_monitor.set()

    def _changed(self, *, seeked=False):
        self._dirty = True
        self._seeked = self._seeked or seeked
        self._wake_sync.set()

    @property
    def ended(self):
        duration = self.current.duration_seconds if self.current else None
        return bool(duration) and not self.paused and self.position_seconds >= duration

    async def stop(self, *, reason):
        if self.finished:
            return
        self.finished = True
        log.info("watch party in %s ended: %s", self.voice_channel_id, reason)
        current = asyncio.current_task()
        for task in self._tasks:
            if task is not current:
                task.cancel()
        await asyncio.gather(*(t for t in self._tasks if t is not current), return_exceptions=True)
        if not self._gave_up:
            try:
                await self.bot.client.end_watch_session(self.voice_channel_id)
            except ApiError as err:
                log.warning("could not end the watch session in %s: %s", self.voice_channel_id, err)
        try:
            await self.voice_session.leave()
        except Exception as err:
            log.warning("could not leave the call in %s: %s", self.voice_channel_id, err)
        if self._on_end is not None:
            await self._on_end(self, reason)

    async def _advance_if_ended(self):
        async with self.lock:
            playing = await self.skip() if not self.finished and self.ended else None
        if playing is not None and self._on_next is not None:
            await self._on_next(self, playing)

    async def _monitor(self):
        """Moves to the next video when one ends, and ends the party once nobody else is on the call."""
        await watch_empty(
            self.bot, self.voice_channel_id, self._wake_monitor, self._on_empty, poll=ROSTER_POLL_SECONDS,
            is_done=lambda: self.finished, on_tick=self._advance_if_ended,
        )

    async def _on_empty(self):
        async with self.lock:
            await self.stop(reason="everyone left the call")

    async def _sync_loop(self):
        loop = asyncio.get_running_loop()
        last_sent = loop.time() - TICK_SECONDS
        while not self._gave_up and not self.finished:
            if not self._dirty:
                await self._wait(last_sent + TICK_SECONDS - loop.time())
            self._wake_sync.clear()
            last_sent = loop.time()
            await self._send()
            await asyncio.sleep(MIN_GAP_SECONDS)

    async def _wait(self, timeout):
        try:
            await asyncio.wait_for(self._wake_sync.wait(), timeout=max(0.0, timeout))
        except asyncio.TimeoutError:
            pass

    async def _send(self):
        if self.current is None:
            return
        seeked = False
        try:
            if self._dirty:
                seeked, self._seeked, self._dirty = self._seeked, False, False
                await self._put(seeked)
            else:
                await self.bot.client.tick_watch_session(
                    self.voice_channel_id, playing=not self.paused, position_ms=int(self.position_seconds * 1000),
                )
        except ApiError as err:
            await self._refused(err, seeked)

    async def _put(self, seeked):
        video = self.current
        await self.bot.client.set_watch_session(
            self.voice_channel_id, item_id=video.video_id, title=wire_title(video.title), playing=not self.paused,
            position_ms=int(self.position_seconds * 1000),
            duration_ms=video.duration_seconds * 1000 if video.duration_seconds else None,
            seeked=seeked, controller_user_id=self._controller, provider=PROVIDER,
        )

    async def _refused(self, err, seeked):
        """A tick's 404 means the session lapsed, so state it again; a 400 drops the controller once, and any refusal that stays ends the party."""
        if err.status == 400 and self._controller is not None:
            log.warning("the server refused controller_user_id in %s (%s); stating the session without one", self.voice_channel_id, err.reason)
            self._controller = None
            self._dirty = True
            self._seeked = self._seeked or seeked
            return
        if err.status in (400, 409):
            log.warning("not syncing the watch party in %s: %s", self.voice_channel_id, err)
            self._gave_up = True
            await self.stop(reason=err.reason or "the server refused the watch session")
            return
        log.warning("watch party write failed in %s: %s", self.voice_channel_id, err)
        self._dirty = True
        self._seeked = self._seeked or seeked
