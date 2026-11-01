"""Lets the encode rest while every viewer plays the title themselves (slim-m decision 0050, stages 3 and 4).
The shared screen share is the floor, so it is lifted only when everyone on the call reports direct play; anything unknown keeps it."""

from __future__ import annotations

import asyncio
import contextlib
import logging

import accounts
import jellyfin_core
from slimbots import ApiError

log = logging.getLogger("jellyfin.direct_play")

POLL_SECONDS = 3.0
# Direct players report a few seconds apart, so a floor lifted on one clean read would flap on a late report.
LIFT_AFTER_SECONDS = 9.0
GIVE_UP_AFTER = 3  # consecutive failed republishes before the party ends rather than sit silent


def build_offer(base_url, kinds):
    """The `direct_play` body: who holds a verified link and who holds one made by typing a name, or None without a URL."""
    if not base_url:
        return None
    return {
        "base_url": base_url,
        "verified_user_ids": sorted(user for user, verified in kinds.items() if verified),
        "unverified_user_ids": sorted(user for user, verified in kinds.items() if not verified),
    }


def needs_floor(people, direct_ids):
    """True when anyone on the call is not playing the title themselves; nobody on the call leaves the floor as it is."""
    return any(person not in direct_ids for person in people)


class DirectPlayHost:
    """One worker per party: offers direct play, watches who takes it, and lifts or restores the shared stream."""

    def __init__(self, session):
        self.session = session
        self._wake = asyncio.Event()
        self._task = None
        self._all_direct_since = None
        self._failed_restores = 0

    def start(self):
        if not jellyfin_core.JELLYFIN_DIRECT_URL:
            return
        self._task = self.session.bot.background(self._run(), name=f"jellyfin-direct-play-{self.session.voice_channel_id}")

    def wake(self):
        """A join or leave on the call: look now instead of at the next poll."""
        self._wake.set()

    async def stop(self):
        task, self._task = self._task, None
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self):
        while not self.session.finished:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=POLL_SECONDS)
            self._wake.clear()
            if self.session.finished:
                return
            try:
                await self._step()
            except Exception as err:
                log.warning("direct play check failed in %s: %s", self.session.voice_channel_id, err)
                await self._restore_if_lifted()

    async def _people(self):
        bot = self.session.bot
        roster = await bot.client.voice_roster(self.session.voice_channel_id)
        ids = [p.get("user_id") for p in roster.get("participants", []) if p.get("user_id") and p.get("user_id") != bot.me_id]
        return [uid for uid in ids if not await bot.authors.is_automated(uid)]

    async def _direct_ids(self):
        try:
            state = await self.session.bot.client.get_watch_session(self.session.voice_channel_id)
        except ApiError:
            return None
        return set(state.get("direct_viewer_ids") or [])

    async def _step(self):
        session = self.session
        people = await self._people()
        kinds = await session.bot.store.run(accounts.link_kinds, people)
        if session.sync is not None:
            session.sync.offer_direct_play(build_offer(jellyfin_core.JELLYFIN_DIRECT_URL, kinds))
        direct = await self._direct_ids()
        if direct is None or not people or needs_floor(people, direct):
            self._all_direct_since = None
            await self._restore_if_lifted()
            return
        loop = asyncio.get_running_loop()
        self._all_direct_since = self._all_direct_since or loop.time()
        if not session.floor_lifted and loop.time() - self._all_direct_since >= LIFT_AFTER_SECONDS:
            await session.lift_floor()

    async def _restore_if_lifted(self):
        session = self.session
        if not session.floor_lifted:
            return
        try:
            await session.restore_floor()
            self._failed_restores = 0
        except Exception as err:
            self._failed_restores += 1
            log.warning("could not put the shared stream back in %s (%s of %s): %s", session.voice_channel_id, self._failed_restores, GIVE_UP_AFTER, err)
            if self._failed_restores >= GIVE_UP_AFTER:
                await session.stop(reason="the shared stream could not be restored")
