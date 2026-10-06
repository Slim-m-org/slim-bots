#!/usr/bin/env python3
"""A post that failed to send is retried even when newer posts landed first; run directly: python3 test_cursor_retry.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_dedupe as d  # noqa: E402

jellyfin, core = d.jellyfin, d.core
ApiError = jellyfin.ApiError


def poll_twice_failing_first(feed, fails):
    """Cycle one fails the sends for which `fails(entry)` is true, cycle two sends everything; returns what landed."""
    state = {"failing": True}
    sent = []

    async def send_post(entry):
        if state["failing"] and fails(entry):
            raise ApiError(500, "boom")
        sent.append(entry["item_ids"])

    saved = (core.items_since, jellyfin.send_post, jellyfin.bot.store)
    core.items_since = lambda cursor: [i for i in feed.items if i["DateCreated"] >= cursor]
    jellyfin.send_post = send_post
    jellyfin.bot.store = d.FakeStore(feed.conn)
    try:
        asyncio.run(jellyfin.poll_once())
        state["failing"] = False
        asyncio.run(jellyfin.poll_once())
    finally:
        core.items_since, jellyfin.send_post, jellyfin.bot.store = saved
    return sent


def test_an_older_post_that_failed_is_retried_after_a_newer_series_post_landed():
    d.with_settings()
    feed = d.Feed()
    feed.items = [
        d.movie("m1", created="2024-01-01T00:00:00Z"),
        d.episode("e1", index=1, created="2024-01-02T00:00:00Z"),
        d.episode("e2", index=2, created="2024-01-02T00:00:01Z"),
    ]
    sent = poll_twice_failing_first(feed, lambda entry: entry["item_ids"] == ["m1"])
    assert ["m1"] in sent, f"movie never announced; sent={sent}"
    assert sent.count(["e1", "e2"]) == 1, f"series announced more than once; sent={sent}"


def test_a_failed_older_post_is_retried_after_a_newer_excluded_item_moved_the_cursor():
    d.with_settings()
    core.JELLYFIN_EXCLUDE_GENRES = {"horror"}
    try:
        feed = d.Feed()
        horror = d.movie("h1", created="2024-01-03T00:00:00Z", name="Hereditary", year=2018)
        horror["Genres"] = ["Horror"]
        feed.items = [d.movie("m1", created="2024-01-01T00:00:00Z"), horror]
        sent = poll_twice_failing_first(feed, lambda entry: entry["item_ids"] == ["m1"])
    finally:
        core.JELLYFIN_EXCLUDE_GENRES = set()
    assert ["m1"] in sent, f"movie never announced; sent={sent}"
    assert ["h1"] not in sent


def test_the_cursor_still_reaches_the_newest_item_once_every_post_landed():
    d.with_settings()
    feed = d.Feed()
    feed.items = [
        d.movie("m1", created="2024-01-01T00:00:00Z"),
        d.movie("m2", created="2024-01-05T00:00:00Z", name="Heat", year=1995),
    ]
    feed.poll()
    assert core.get_cursor(feed.conn) == "2024-01-05T00:00:00Z"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
