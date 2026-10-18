#!/usr/bin/env python3
"""`!watch` start-up: one party per call even when two start together, and a failed start leaves the call; run directly: python3 test_watch_launch.py."""

import asyncio
import errno

import jellyfin_core
import session_registry
import stream_session
from test_bot import jellyfin, message, movie_for_watch, asynced
from test_resume import Patched, voice_client
from slimbots.testing import human, run_module_tests

FOUND = (
    (jellyfin_core, "watch_search", asynced(lambda query, limit: [movie_for_watch()])),
    (jellyfin_core, "fetch_item_for_playback", asynced(lambda item_id, user_id=None: movie_for_watch())),
)


async def full_disk(self, start_seconds=0.0):
    raise OSError(errno.ENOSPC, "No space left on device")


def watch_from(author_id, message_id):
    return {**message("!watch inception", message_id), "author_id": author_id}


def test_two_members_starting_a_watch_in_one_call_get_one_party():
    client = voice_client()
    jellyfin.bot.voice.member_channels = {"u1": "v1", "u2": "v1"}
    client.respond("GET", "/users/u2", human("u2", "sam", "Sam"))
    starts = []

    async def slow_start(self, start_seconds=0.0):
        starts.append(self.voice_session)
        await asyncio.sleep(0.05)

    async def both():
        await asyncio.gather(jellyfin.bot.process_message(watch_from("u1", "m1")), jellyfin.bot.process_message(watch_from("u2", "m2")))

    with Patched((stream_session.WatchSession, "start", slow_start), *FOUND):
        asyncio.run(both())
    joins = len(jellyfin.bot.voice.sessions)
    assert joins == 1 and len(starts) == 1, f"{joins} joins, {len(starts)} starts"
    assert any("already" in (m.get("content") or "") for m in client.ephemerals)


def test_a_start_failure_that_is_not_a_stream_error_still_leaves_the_call_and_replies():
    client = voice_client()

    with Patched((stream_session.WatchSession, "start", full_disk), *FOUND):
        asyncio.run(jellyfin.bot.process_message(message("!watch inception")))
    voices = list(jellyfin.bot.voice.sessions)
    assert voices and all(v.left for v in voices), "bot is still in the call after the start failed"
    assert session_registry.session_for_channel("v1") is None
    assert any("could not start streaming" in (m.get("content") or "") for m in client.ephemerals)


def test_a_failed_start_frees_the_call_for_the_next_try():
    voice_client()
    jellyfin.bot.voice.join_error = None

    async def twice():
        await jellyfin.bot.process_message(watch_from("u1", "m1"))
        await jellyfin.bot.process_message(watch_from("u1", "m2"))

    with Patched((stream_session.WatchSession, "start", full_disk), *FOUND):
        asyncio.run(twice())
    assert len(jellyfin.bot.voice.sessions) == 2


if __name__ == "__main__":
    run_module_tests(globals())
