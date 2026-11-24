#!/usr/bin/env python3
"""A highlight follows its original through failed deletes, channels that turn private and missed frames; run directly: python3 test_consistency.py."""

import asyncio
import os
import time

os.environ["STARBOARD_CHANNEL"] = "hl"
import bot as starboard  # noqa: E402
from test_bot import created, db, frame, react, setup  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

HL = starboard.highlight_id("m1")


def starred_once(content="hello world"):
    client = setup()
    created(content=content)
    react(3)
    client.respond("PATCH", f"/channels/hl/messages/{HL}", {})
    return client


def deletes(client):
    return [c for c in client.calls if c[0] == "DELETE" and c[1] == f"/channels/hl/messages/{HL}"]


def patches(client):
    return [c for c in client.calls if c[0] == "PATCH"]


def due_digest(client):
    client.sent.clear()
    asyncio.run(starboard.bot.store.run(starboard.meta_set, "last_digest", int(time.time()) - 8 * 86400))
    asyncio.run(starboard.post_digest_if_due(int(time.time())))
    return client.sent


def test_a_failed_highlight_delete_is_retried_until_the_highlight_is_gone():
    client = starred_once("secret plans")
    client.respond("DELETE", f"/channels/hl/messages/{HL}", ApiError(503, {"error": "unavailable"}))
    frame("message.deleted", channel_id="c1", message_id="m1")
    assert len(deletes(client)) == 1
    assert db("SELECT content FROM starred") == [("",)], "the deleted text must not wait in the store for the retry"
    assert db("SELECT * FROM seen") == []
    client.respond("DELETE", f"/channels/hl/messages/{HL}", None)
    asyncio.run(starboard.maintenance())
    assert len(deletes(client)) == 2
    assert db("SELECT * FROM starred") == []


def test_a_repeated_delete_event_does_not_drop_a_pending_delete():
    client = starred_once()
    client.respond("DELETE", f"/channels/hl/messages/{HL}", ApiError(503, {"error": "unavailable"}))
    frame("message.deleted", channel_id="c1", message_id="m1")
    frame("message.deleted", channel_id="c1", message_id="m1")
    client.respond("DELETE", f"/channels/hl/messages/{HL}", None)
    asyncio.run(starboard.maintenance())
    assert len(deletes(client)) == 2
    assert db("SELECT * FROM starred") == []


def test_a_highlight_someone_already_removed_is_forgotten():
    client = starred_once()
    client.respond("DELETE", f"/channels/hl/messages/{HL}", ApiError(404, {"error": "message not found"}))
    frame("message.deleted", channel_id="c1", message_id="m1")
    assert db("SELECT * FROM starred") == []


def test_a_highlight_waiting_to_be_deleted_is_left_out_of_the_digest_and_edits():
    client = starred_once("secret plans")
    client.respond("DELETE", f"/channels/hl/messages/{HL}", ApiError(503, {"error": "unavailable"}))
    frame("message.deleted", channel_id="c1", message_id="m1")
    react(5)
    assert patches(client) == []
    assert [c for c in client.calls if c[0] == "GET" and "/messages/" in c[1]] == []
    assert due_digest(client) == []


def test_an_edit_after_the_channel_turned_private_is_not_mirrored():
    client = starred_once("before")
    starboard.bot.space.channels["c1"].restricted = True
    frame("message.edited", channel_id="c1", seq=2, message={"id": "m1", "content": "written once private"})
    react(5)
    assert patches(client) == []


def test_the_digest_skips_a_channel_that_turned_private():
    client = starred_once("before")
    starboard.bot.space.channels["c1"].restricted = True
    assert due_digest(client) == []


def test_the_digest_still_fills_its_slots_around_a_private_channel():
    client = setup()
    created("m1", "public one")
    created("m2", "now private", channel_id="priv")
    react(3, "m1")
    asyncio.run(starboard.bot.store.run(starboard.add_starred, ("m2", "priv", "u1", "now private", 0, ""), "h2", 9))
    sent = due_digest(client)
    assert len(sent) == 1
    assert "public one" in sent[0]["content"] and "now private" not in sent[0]["content"]


def test_a_delete_missed_while_disconnected_is_applied_on_reconnect():
    client = starred_once()
    client.respond("GET", "/channels/c1/messages/m1", ApiError(404, {"error": "message not found"}))
    client.respond("DELETE", f"/channels/hl/messages/{HL}", None)
    asyncio.run(starboard.recheck_starred())
    assert len(deletes(client)) == 1
    assert db("SELECT * FROM starred") == []


def test_an_edit_missed_while_disconnected_is_applied_on_reconnect():
    client = starred_once("before")
    client.respond("GET", "/channels/c1/messages/m1", {"id": "m1", "author_id": "u1", "content": "after", "attachments": []})
    asyncio.run(starboard.recheck_starred())
    assert len(patches(client)) == 1
    assert "> after" in patches(client)[0][2]["content"]
    assert db("SELECT content FROM starred") == [("after",)]


def test_an_unchanged_original_is_not_rewritten_on_reconnect():
    client = starred_once("same")
    client.respond("GET", "/channels/c1/messages/m1", {"id": "m1", "author_id": "u1", "content": "same", "attachments": []})
    asyncio.run(starboard.recheck_starred())
    assert patches(client) == []


def test_a_reconnect_survives_one_original_that_cannot_be_read():
    client = starred_once()
    created("m2", "second")
    react(3, "m2")
    client.respond("GET", "/channels/c1/messages/m1", ApiError(503, {"error": "unavailable"}))
    client.respond("GET", "/channels/c1/messages/m2", ApiError(404, {"error": "message not found"}))
    client.respond("DELETE", f"/channels/hl/messages/{starboard.highlight_id('m2')}", None)
    asyncio.run(starboard.recheck_starred())
    assert [r[0] for r in db("SELECT message_id FROM starred")] == ["m1"]


def test_an_existing_database_gains_the_pending_delete_column():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE starred (message_id TEXT PRIMARY KEY, channel_id TEXT, author_id TEXT, content TEXT, attachments INTEGER, highlight_id TEXT, count INTEGER, starred_at INTEGER)")
    starboard.init_db(conn)
    assert "pending_delete" in {row[1] for row in conn.execute("PRAGMA table_info(starred)")}


def test_per_message_locks_do_not_outlive_their_events():
    client = starred_once()
    react(5)
    frame("message.edited", channel_id="c1", seq=2, message={"id": "m1", "content": "after"})
    client.respond("DELETE", f"/channels/hl/messages/{HL}", None)
    frame("message.deleted", channel_id="c1", message_id="m1")
    created("m2")
    react(1, "m2")
    assert starboard._locks == {}


if __name__ == "__main__":
    run_module_tests(globals())
