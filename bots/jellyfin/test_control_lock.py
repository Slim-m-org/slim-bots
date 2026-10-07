#!/usr/bin/env python3
"""A typed command and a panel press serialise on the session's control lock; run directly: python3 test_control_lock.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_seek as ts  # noqa: E402
import watch_cog  # noqa: E402


class Author:
    id = "u1"
    display_name = "Nick"

    def has_permission(self, _permission):
        return True


class Ctx:
    def __init__(self):
        self.bot = ts.jellyfin.bot
        self.author = Author()
        self.replies = []
        self.channel_id = "c1"
        self.message = {"id": "m1"}

    async def reply(self, text=None, **_kwargs):
        self.replies.append(text)

    async def reply_ephemeral(self, text="", **_kwargs):
        self.replies.append(text)


def slow_launch(original):
    async def launch(self, *args, **kwargs):
        await asyncio.sleep(0.01)
        return await original(self, *args, **kwargs)

    return launch


def live_pipeline(rig):
    processes = [p for p in rig.jf.processes if p.returncode is None]
    pumps = [t for t in asyncio.all_tasks() if t.get_name() in ("jellyfin-video-pump", "jellyfin-audio-pump") and not t.done()]
    return len(processes), len(pumps)


def race_a_press_against(typed_command):
    original = ts.FakeJellyfin.create_subprocess_exec
    ts.FakeJellyfin.create_subprocess_exec = slow_launch(original)
    live = []

    @ts.with_rig
    def run(rig):
        async def scenario():
            await rig.start()
            ts.jellyfin.bot.voice.find_member = lambda _user_id: asyncio.sleep(0, rig.session.voice_channel_id)
            await asyncio.gather(typed_command(Ctx()), rig.press("fwd"))
            live = live_pipeline(rig)
            await rig.session._teardown_pipeline()
            return live

        live.append(rig.run(scenario()))

    try:
        run()
    finally:
        ts.FakeJellyfin.create_subprocess_exec = original
    return live[0]


def clean_pipeline():
    live = []

    @ts.with_rig
    def run(rig):
        async def scenario():
            await rig.start()
            shape = live_pipeline(rig)
            await rig.session._teardown_pipeline()
            return shape

        live.append(rig.run(scenario()))

    run()
    return live[0]


def test_a_typed_seek_racing_a_panel_press_leaves_one_pipeline():
    raced = race_a_press_against(lambda ctx: watch_cog.run_seek(ctx, "1:00"))
    assert raced == clean_pipeline()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
