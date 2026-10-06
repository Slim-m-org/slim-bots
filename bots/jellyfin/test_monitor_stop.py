#!/usr/bin/env python3
"""stop() called from the monitor task must not cancel that task; run directly: python3 test_monitor_stop.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import playback_progress  # noqa: E402
import stream_session  # noqa: E402
from slimbots.testing import FakeVoiceSession  # noqa: E402
from test_bot import jellyfin, movie_for_watch, setup_with_voice  # noqa: E402


class Exited:
    returncode = 0

    async def communicate(self):
        return b"", b""


def test_an_empty_call_while_waiting_for_next_leaves_the_room_without_cancelling_the_monitor():
    setup_with_voice()
    saved = playback_progress.report_position, playback_progress.resolve_user_id
    playback_progress.report_position = lambda *args, **kwargs: None
    playback_progress.resolve_user_id = lambda: "u1"
    voice = FakeVoiceSession("c1")
    session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", voice)
    seen = {}

    async def roster(_channel_id):
        return {"participants": [{"user_id": jellyfin.bot.me_id}]}

    jellyfin.bot.client.voice_roster = roster

    async def monitor_then_report():
        await session._monitor_loop()
        seen["cancel_requests"] = asyncio.current_task().cancelling()

    async def scenario():
        async def noop():
            return None

        session._video_task = asyncio.create_task(noop())
        session._audio_task = asyncio.create_task(noop())
        await asyncio.sleep(0)
        session._video_process = session._audio_process = Exited()
        session.waiting_next = {"Id": "e2", "Name": "next"}
        session.paused = True
        session._monitor_task = asyncio.create_task(monitor_then_report())
        session.wake_monitor()
        try:
            await asyncio.wait_for(asyncio.shield(session._monitor_task), timeout=2)
        except asyncio.CancelledError:
            seen["cancelled"] = True

    try:
        asyncio.run(scenario())
    finally:
        playback_progress.report_position, playback_progress.resolve_user_id = saved
    assert not seen.get("cancelled") and seen["cancel_requests"] == 0, "stop() cancelled the task it runs in"
    assert voice.left, "the call is empty but the bot never left the livekit room"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
