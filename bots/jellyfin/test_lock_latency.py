#!/usr/bin/env python3
"""A slow jellyfin must not hold the store lock: the poll and the cold-start bootstrap fetch off it; run directly: python3 test_lock_latency.py."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import accounts  # noqa: E402
from test_bot import jellyfin, setup  # noqa: E402

core = jellyfin.jellyfin_core
SLOW_SECONDS = 0.6
MAX_WAIT_SECONDS = 0.25


def slow_jf_get(path, params=None):
    time.sleep(SLOW_SECONDS)
    return {"Items": []}


async def wait_for_a_lookup_during(slow_call):
    """How long a plain store lookup waits when it starts 0.1s into `slow_call`."""
    task = asyncio.create_task(slow_call())
    await asyncio.sleep(0.1)
    started = time.monotonic()
    await jellyfin.bot.store.run(accounts.get_link, "u1")
    waited = time.monotonic() - started
    await task
    return waited


def with_slow_jellyfin(scenario):
    saved = core.jf_get
    core.jf_get = slow_jf_get
    try:
        return asyncio.run(scenario())
    finally:
        core.jf_get = saved


def test_a_lookup_is_not_blocked_by_a_slow_poll():
    setup()

    async def scenario():
        await jellyfin.bot.store.run(core.advance_cursor, "2000-01-01T00:00:00.0000000Z")
        return await wait_for_a_lookup_during(jellyfin.poll_once)

    waited = with_slow_jellyfin(scenario)
    assert waited < MAX_WAIT_SECONDS, f"get_link waited {waited:.2f}s behind a poll"


def test_a_lookup_is_not_blocked_by_a_slow_cold_start_bootstrap():
    setup()
    waited = with_slow_jellyfin(lambda: wait_for_a_lookup_during(jellyfin.bootstrap))
    assert waited < MAX_WAIT_SECONDS, f"get_link waited {waited:.2f}s behind the bootstrap"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
