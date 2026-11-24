#!/usr/bin/env python3
"""`!jellyfin search` and `!watch <title>` stay inside the configured libraries, genres and the member's own access; run directly: python3 test_search_scope.py."""

import asyncio

import httpx

import accounts
import jellyfin_core
from slimbots.testing import run_module_tests
from test_bot import asynced, jellyfin, message, movie_for_watch, process, setup
from test_resume import Patched, run_watch_pressing, voice_client


def items_server(requests):
    """A fake `/Items` that records each query and answers one plain and one horror title."""
    def answer(request):
        requests.append(dict(request.url.params))
        return httpx.Response(200, json={"Items": [
            {"Id": "m1", "Name": "Inception", "Type": "Movie", "Genres": ["Science Fiction"]},
            {"Id": "m2", "Name": "Hereditary", "Type": "Movie", "Genres": ["Horror"]},
        ]})
    return httpx.MockTransport(answer)


def scoped(requests):
    return Patched(
        (jellyfin_core, "TRANSPORT", items_server(requests)),
        (jellyfin_core, "JELLYFIN_LIBRARY_IDS", ["lib-a"]),
        (jellyfin_core, "JELLYFIN_EXCLUDE_GENRES", {"horror"}),
    )


def test_search_stays_inside_the_configured_libraries_and_the_members_own_access():
    client = setup()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice", True))
    requests = []
    with scoped(requests):
        process(client, message("!jellyfin search e"))
    assert [(r.get("parentId"), r.get("userId")) for r in requests] == [("lib-a", "jf-a")]
    reply = client.sent[-1]["content"]
    assert "Inception" in reply and "Hereditary" not in reply


def test_search_by_an_unlinked_member_runs_as_the_shared_account():
    client = setup()
    requests = []
    with scoped(requests):
        process(client, message("!jellyfin search e"))
    assert requests[0]["userId"] == "jf-user"


def test_watch_search_drops_excluded_genres_and_asks_each_configured_library():
    requests = []
    with scoped(requests), Patched((jellyfin_core, "JELLYFIN_LIBRARY_IDS", ["lib-a", "lib-b"])):
        found = asyncio.run(jellyfin_core.watch_search("e", 8, "jf-a"))
    assert [item["Id"] for item in found] == ["m1"]
    assert [r["parentId"] for r in requests] == ["lib-a", "lib-b"]
    assert all(r["userId"] == "jf-a" for r in requests)


def test_watch_searches_as_the_member_who_asked():
    voice_client()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice", True))
    searched_as = []

    def search(query, limit, user_id=None):
        searched_as.append(user_id)
        return [movie_for_watch()]

    with Patched(
        (jellyfin_core, "watch_search", asynced(search)),
        (jellyfin_core, "fetch_item_for_playback", asynced(lambda item_id, user_id=None: movie_for_watch())),
    ):
        run_watch_pressing("!watch inception")
    assert searched_as == ["jf-a"]


if __name__ == "__main__":
    run_module_tests(globals())
