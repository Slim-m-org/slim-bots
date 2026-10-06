#!/usr/bin/env python3
"""Media lookups never hold the store lock, and a dropped seerr never silences a command; run directly: python3 test_details.py."""

import asyncio
import os
import sys
import time
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import seerr_core as core  # noqa: E402
from arrkit.service import AuthError  # noqa: E402
from arrkit.testkit import Patched  # noqa: E402
from test_bot import message, poll, process, request, seerr, seerr_api, seerr_cog, setup  # noqa: E402

DELAY = 0.4


def slow_movie_lookups(real):
    def api(method, path, params=None, body=None):
        if path.startswith("/movie/"):
            time.sleep(DELAY)
        return real(method, path, params, body)

    return api


def failing_movie_lookups(real, error):
    def api(method, path, params=None, body=None):
        if path.startswith("/movie/"):
            raise error
        return real(method, path, params, body)

    return api


def test_the_store_stays_usable_while_the_poller_looks_up_media():
    setup()
    fake = seerr_api()
    fake.requests = [request(1, status=2, media_status=5)]
    poll(fake)
    fake.requests += [request(2, tmdb=2), request(3, tmdb=3), request(4, tmdb=4)]
    waited = {}

    async def go():
        poller = asyncio.create_task(seerr.poll_once())
        await asyncio.sleep(0.05)
        start = time.perf_counter()
        await seerr.bot.store.run(core.get_link, "u1")
        waited["seconds"] = time.perf_counter() - start
        await poller

    with Patched(core, "api", slow_movie_lookups(fake)):
        asyncio.run(go())
    assert waited["seconds"] < DELAY / 2, f"get_link blocked {waited['seconds']:.2f}s"


def test_one_title_requested_twice_is_looked_up_once():
    client = setup()
    fake = seerr_api()
    fake.requests = [request(1, status=2, media_status=5)]
    poll(fake)
    fake.requests += [request(2, tmdb=7), request(3, tmdb=7, by="bob")]
    poll(fake)
    assert len(client.sent) == 2
    assert len([c for c in fake.calls if c[1] == "/movie/7"]) == 1


def test_a_dropped_seerr_makes_the_poller_retry_instead_of_posting_a_placeholder():
    client = setup()
    fake = seerr_api()
    fake.requests = [request(1, status=2, media_status=5)]
    poll(fake)
    fake.requests.append(request(2, tmdb=7))
    try:
        poll(failing_movie_lookups(fake, urllib.error.URLError("reset")))
    except urllib.error.URLError:
        pass
    assert client.sent == []
    poll(fake)
    assert "Title 7" in client.sent[-1]["content"], client.sent[-1]["content"]


def test_requests_replies_with_a_placeholder_when_seerr_drops_mid_list():
    client = setup()
    fake = seerr_api()
    fake.pending = {"results": [request(1, tmdb=7)], "pageInfo": {"results": 1}}
    for error in (urllib.error.URLError("reset"), TimeoutError("slow"), AuthError("rejected")):
        seerr_cog.GUARD.cooldown._last.clear()
        before = len(client.sent)
        with Patched(core, "api", failing_movie_lookups(fake, error)):
            process(message("!requests"))
        assert len(client.sent) == before + 1 and "tmdb 7" in client.sent[-1]["content"], error


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
