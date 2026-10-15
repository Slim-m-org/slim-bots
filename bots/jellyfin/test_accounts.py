#!/usr/bin/env python3
"""Per-member Jellyfin accounts: link, unlink, and who resume and progress belong to; run directly: python3 test_accounts.py."""

import asyncio
import sqlite3

import accounts
import jellyfin_core
import playback_progress
import session_registry
import stream_session
from slimbots.testing import FakeVoiceSession, run_module_tests
from test_bot import jellyfin, message, movie_for_watch, process, setup, asynced
from test_panel import client_in_call, episode_for_watch
from test_resume import Patched, run_watch_pressing, voice_client, with_position
import httpx

def link_of(user):
    return asyncio.run(jellyfin.bot.store.run(accounts.get_link, user))


def test_unlink_removes_it_and_says_so_when_there_was_nothing():
    client = setup()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice"))
    process(client, message("!jellyfin unlink"))
    assert link_of("u1") is None and "unlinked" in client.ephemerals[-1]["content"]
    process(client, message("!jellyfin unlink"))
    assert client.ephemerals[-1]["content"] == "you were not linked to a jellyfin user."


def test_account_reports_the_link_privately():
    client = setup()
    process(client, message("!jellyfin account"))
    assert "not linked" in client.ephemerals[-1]["content"]
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice", True))
    with Patched((jellyfin_core, "jf_get", asynced(lambda path, params=None: {"Id": "jf-a", "Name": "Alice"}))):
        process(client, message("!jellyfin account"))
    assert 'linked to the jellyfin user "Alice"' in client.ephemerals[-1]["content"]


def test_account_hints_name_the_configured_prefix():
    client = setup()
    original, jellyfin.bot.prefix = jellyfin.bot.prefix, "?"
    try:
        process(client, message("?jellyfin account"))
    finally:
        jellyfin.bot.prefix = original
    assert "`?watch` uses the shared" in client.ephemerals[-1]["content"]
    assert "`?jellyfin link` links you" in client.ephemerals[-1]["content"]


def test_user_for_prefers_the_link_and_falls_back_to_the_shared_account():
    setup()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice"))
    assert asyncio.run(accounts.user_for(jellyfin.bot, "u1")) == "jf-a"
    assert asyncio.run(accounts.user_for(jellyfin.bot, "u2")) == "jf-user"


def test_watch_reads_the_resume_position_of_the_linked_user():
    client = voice_client()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice"))
    fetched = []

    async def fetch(item_id, user_id=None):
        fetched.append(user_id)
        return with_position(movie_for_watch(), 600)

    with Patched(
        (jellyfin_core, "watch_search", asynced(lambda query, limit: [movie_for_watch()])),
        (jellyfin_core, "fetch_item_for_playback", fetch),
    ):
        started = run_watch_pressing("!watch inception", "jfp:resume")
    assert fetched == ["jf-a"] and started == [600.0]


def test_watch_without_a_title_continues_the_linked_users_last_item():
    client = voice_client()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice"))
    asked = []

    async def last(user_id=None):
        asked.append(user_id)
        return None

    with Patched((playback_progress, "fetch_last_watched", last)):
        run_watch_pressing("!watch")
    assert asked == ["jf-a"]


def test_a_member_without_a_link_still_uses_the_shared_account():
    voice_client()
    fetched = []

    async def fetch(item_id, user_id=None):
        fetched.append(user_id)
        return movie_for_watch()

    with Patched(
        (jellyfin_core, "watch_search", asynced(lambda query, limit: [movie_for_watch()])),
        (jellyfin_core, "fetch_item_for_playback", fetch),
    ):
        run_watch_pressing("!watch inception")
    assert fetched == ["jf-user"]


def test_progress_goes_to_the_account_of_whoever_started_the_stream():
    posted = []
    voice_client()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice"))

    async def fake_start(self, start_seconds=0.0):
        return None

    with Patched(
        (jellyfin_core, "watch_search", asynced(lambda query, limit: [movie_for_watch()])),
        (jellyfin_core, "fetch_item_for_playback", asynced(lambda item_id, user_id=None: movie_for_watch())),
        (stream_session.WatchSession, "start", fake_start),
        (jellyfin_core, "TRANSPORT", httpx.MockTransport(lambda request: posted.append((request.url.path, dict(request.url.params))) or httpx.Response(204))),
    ):
        process(jellyfin.bot.client, message("!watch inception"))
        session = session_registry.session_for_channel("v1")
        assert session.jellyfin_user_id == "jf-a"
        session.voice_session = FakeVoiceSession("v1")
        session._teardown_pipeline = lambda: asyncio.sleep(0)
        asyncio.run(session.stop(announce=False))
    assert posted == [("/UserItems/m1/UserData", {"userId": "jf-a"})]


def test_the_next_episode_lookup_uses_the_starters_account():
    client = client_in_call()
    seen = []
    session = stream_session.WatchSession(jellyfin.bot, "c1", "v1", episode_for_watch(), "u1", FakeVoiceSession("v1"))
    session.jellyfin_user_id = "jf-a"
    with Patched((jellyfin_core, "next_episode", asynced(lambda item, user_id=None: seen.append(user_id)))):
        asyncio.run(session._upcoming_episode())
    assert seen == ["jf-a"] and client


def test_init_db_adds_the_links_table_to_an_older_database_without_touching_the_rest():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE state (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE posted_items (item_id TEXT PRIMARY KEY, posted_at INTEGER NOT NULL);"
    )
    conn.execute("INSERT INTO posted_items VALUES ('m1', 1)")
    conn.commit()
    jellyfin_core.init_db(conn)
    accounts.set_link(conn, "u1", "jf-a", "Alice")
    assert accounts.get_link(conn, "u1") == ("jf-a", "Alice")
    assert jellyfin_core.already_posted(conn, "m1")


if __name__ == "__main__":
    run_module_tests(globals())
