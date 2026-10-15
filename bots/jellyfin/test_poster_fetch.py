#!/usr/bin/env python3
"""A poster fetch that fails returns None and says why on stderr, instead of failing silently."""

import asyncio
import contextlib
import io
import os
import sys

os.environ.setdefault("JELLYFIN_URL", "https://fake-jellyfin.invalid")
os.environ.setdefault("JELLYFIN_API_KEY", "fake-key")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as jellyfin  # noqa: E402,F401
import httpx  # noqa: E402
import jellyfin_core  # noqa: E402


def fetch_with(error):
    def refuse(request):
        raise error

    original = jellyfin_core.TRANSPORT
    jellyfin_core.TRANSPORT = httpx.MockTransport(refuse)
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            result = asyncio.run(jellyfin_core.jf_get_bytes("/Items/i1/Images/Primary?api_key=secret"))
    finally:
        jellyfin_core.TRANSPORT = original
    return result, stderr.getvalue()


def test_a_network_failure_returns_none_and_is_logged_without_the_query():
    result, logged = fetch_with(httpx.ConnectError("no route"))
    assert result is None
    assert "/Items/i1/Images/Primary failed: ServiceUnreachable" in logged and "ConnectError" in logged
    assert "secret" not in logged


def test_a_timeout_returns_none_and_is_logged():
    result, logged = fetch_with(httpx.ReadTimeout("timed out"))
    assert result is None and "ReadTimeout" in logged


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
