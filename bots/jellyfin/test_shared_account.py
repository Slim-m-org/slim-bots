#!/usr/bin/env python3
"""The shared account unlinked members watch as is named, and never a jellyfin administrator; run directly: python3 test_shared_account.py."""

import asyncio

import httpx

import accounts
import jellyfin_core
import playback_progress
from slimbots.testing import run_module_tests
from test_bot import asynced, jellyfin, message, movie_for_watch, process, setup
from test_resume import Patched, run_watch_pressing, voice_client


def users_server(policy, calls=None):
    """A fake `GET /Users/{id}` answering one user with `policy`, or 404 when `policy` is None."""
    def answer(request):
        if calls is not None:
            calls.append(request.url.path)
        if policy is None:
            return httpx.Response(404, json={})
        return httpx.Response(200, json={"Id": request.url.path.rsplit("/", 1)[-1], "Name": "Shared", "Policy": policy})
    return httpx.MockTransport(answer)


def shared(user_id, policy, calls=None):
    return Patched(
        (jellyfin_core, "TRANSPORT", users_server(policy, calls)),
        (playback_progress, "JELLYFIN_USER_ID", user_id), (playback_progress, "_checked_user_id", None),
    )


def test_startup_refuses_an_administrator_as_the_shared_account():
    with shared("jf-admin", {"IsAdministrator": True}):
        problem = jellyfin.check_shared_account()
    assert problem and "JELLYFIN_USER_ID" in problem and "administrator" in problem


def test_startup_refuses_a_shared_account_jellyfin_does_not_know_or_has_disabled():
    with shared("jf-gone", None):
        assert "JELLYFIN_USER_ID" in jellyfin.check_shared_account()
    with shared("jf-off", {"IsDisabled": True}):
        assert "JELLYFIN_USER_ID" in jellyfin.check_shared_account()


def test_startup_accepts_an_ordinary_shared_account_and_needs_no_second_lookup():
    calls = []
    with shared("jf-guest", {"IsAdministrator": False}, calls):
        assert jellyfin.check_shared_account() is None
        assert asyncio.run(playback_progress.resolve_user_id()) == "jf-guest"
    assert calls == ["/Users/jf-guest"]


def test_startup_goes_ahead_when_jellyfin_is_down_and_the_account_is_checked_at_first_use():
    def down(request):
        raise httpx.ConnectError("refused")

    with shared("jf-admin", {"IsAdministrator": True}), Patched((jellyfin_core, "TRANSPORT", httpx.MockTransport(down))):
        assert jellyfin.check_shared_account() is None
    with shared("jf-admin", {"IsAdministrator": True}):
        assert asyncio.run(playback_progress.resolve_user_id()) is None


def test_with_no_shared_account_set_nobody_borrows_the_first_jellyfin_user():
    calls = []
    with shared("", {"IsAdministrator": True}, calls):
        assert jellyfin.check_shared_account() is None
        assert asyncio.run(playback_progress.resolve_user_id()) is None
    assert calls == []


def test_an_unlinked_member_is_asked_to_link_when_there_is_no_shared_account():
    client = voice_client()
    searched = []
    with Patched(
        (playback_progress, "JELLYFIN_USER_ID", ""),
        (jellyfin_core, "watch_search", asynced(lambda query, limit, user_id=None: searched.append(query) or [movie_for_watch()])),
    ):
        run_watch_pressing("!watch inception")
    assert searched == []
    assert "!jellyfin link" in (client.ephemerals or client.sent)[-1]["content"]


def test_a_linked_member_still_watches_when_there_is_no_shared_account():
    voice_client()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice", True))
    with Patched(
        (playback_progress, "JELLYFIN_USER_ID", ""),
        (jellyfin_core, "watch_search", asynced(lambda query, limit, user_id=None: [movie_for_watch()])),
        (jellyfin_core, "fetch_item_for_playback", asynced(lambda item_id, user_id=None: movie_for_watch())),
    ):
        started = run_watch_pressing("!watch inception")
    assert started == [0.0]


def test_account_says_linking_is_needed_when_there_is_no_shared_account():
    client = setup()
    with Patched((playback_progress, "JELLYFIN_USER_ID", "")):
        process(client, message("!jellyfin account"))
    text = client.ephemerals[-1]["content"]
    assert "no shared jellyfin account" in text and "!jellyfin link" in text


if __name__ == "__main__":
    run_module_tests(globals())
