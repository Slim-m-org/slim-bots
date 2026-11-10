"""Live message seqs are noted in memory and written together, off the frame path, and a locked file never breaks a frame."""

import asyncio
import logging
import sqlite3

from slimbots import Bot, cursor
from slimbots import bot as botmod
from slimbots.authors import AuthorFilter
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

MEMBER = {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}


def message_frame(msg_id, seq, channel="c1", content="!ping"):
    return {
        "type": "message.created", "channel_id": channel,
        "message": {"id": msg_id, "author_id": "u1", "channel_id": channel, "content": content, "seq": seq},
    }


def connect(path, timeout=0.05):
    conn = sqlite3.connect(path, isolation_level=None, timeout=timeout)
    cursor.init_table(conn)
    return conn


async def a_bot(tmp_path, *, timeout=0.05):
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", [MEMBER])
    bot = Bot(channels={"c1", "c2"}, prefix="!")
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space)
    path = str(tmp_path / "cursor.db")
    bot._cursor_conn = connect(path, timeout)
    await bot.space.refresh_members()
    return bot, client, path


async def test_a_burst_of_messages_costs_one_transaction_not_one_per_message(tmp_path):
    bot, _client, _path = await a_bot(tmp_path)
    statements = []
    bot._cursor_conn.set_trace_callback(statements.append)
    for number in range(1, 41):
        bot._note_seq("c1" if number % 2 else "c2", number)
    assert statements == [], "a message wrote to sqlite on the spot"
    bot._flush_cursor()
    assert statements.count("BEGIN") == 1 and statements.count("COMMIT") == 1
    assert cursor.get(bot._cursor_conn, "c1") == 39 and cursor.get(bot._cursor_conn, "c2") == 40


async def test_the_noted_seqs_are_written_shortly_after_without_anyone_asking(tmp_path, monkeypatch):
    monkeypatch.setattr(botmod, "CURSOR_FLUSH_SECONDS", 0.01)
    bot, _client, _path = await a_bot(tmp_path)
    bot._note_seq("c1", 7)
    bot._note_seq("c1", 5)
    await asyncio.sleep(0.1)
    assert cursor.get(bot._cursor_conn, "c1") == 7


async def test_a_live_message_while_the_cursor_file_is_locked_still_runs_its_command(tmp_path, caplog):
    bot, client, path = await a_bot(tmp_path)
    ran = []

    @bot.command()
    async def ping(ctx):
        ran.append(ctx.message["seq"])

    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    try:
        with caplog.at_level(logging.WARNING):
            await asyncio.wait_for(bot._handle_frame(message_frame("m1", 1)), 1)
            await asyncio.gather(*list(bot._background_tasks))
            bot._flush_cursor()
    finally:
        holder.rollback()
        holder.close()
    assert ran == [1]
    assert any("could not save the message cursor" in record.getMessage() for record in caplog.records)
    assert cursor.get(bot._cursor_conn, "c1") is None


async def test_a_seq_whose_write_failed_is_written_on_the_next_try(tmp_path, monkeypatch):
    monkeypatch.setattr(botmod, "CURSOR_RETRY_SECONDS", 0.01)
    bot, _client, path = await a_bot(tmp_path)
    holder = sqlite3.connect(path, isolation_level=None)
    holder.execute("BEGIN IMMEDIATE")
    bot._note_seq("c1", 9)
    bot._flush_cursor()
    holder.rollback()
    holder.close()
    await asyncio.sleep(0.2)
    assert cursor.get(bot._cursor_conn, "c1") == 9


async def test_a_catch_up_asks_from_the_seq_the_live_path_just_noted(tmp_path):
    bot, client, _path = await a_bot(tmp_path)
    asked = []

    def sync(body):
        asked.append(body["scopes"])
        return {"scopes": [{"channel_id": s["channel_id"], "messages": [], "has_more": False, "reset": False} for s in body["scopes"]]}

    async def call(method, path, body=None, **kw):
        if (method, path) == ("POST", "/sync"):
            return sync(body)
        return await FakeAsyncClient.call(client, method, path, body, **kw)

    client.call = call
    cursor.set(bot._cursor_conn, "c2", 0)
    bot._note_seq("c1", 12)
    await bot._catch_up()
    assert {s["channel_id"]: s["after_seq"] for s in asked[0]}["c1"] == 12


async def test_shutdown_writes_what_is_still_noted(tmp_path):
    bot, _client, path = await a_bot(tmp_path)
    bot._note_seq("c1", 21)
    bot._close_cursor()
    reread = connect(path)
    assert cursor.get(reread, "c1") == 21
    assert bot._cursor_flush is None


def test_the_pending_writer_never_moves_a_seq_backwards_and_skips_an_empty_flush(tmp_path):
    conn = connect(str(tmp_path / "cursor.db"))
    pending = cursor.Pending()
    pending.flush(conn)
    cursor.set(conn, "c1", 10)
    pending.note("c1", 4)
    pending.note("c2", 0)
    pending.flush(conn)
    assert cursor.get(conn, "c1") == 10 and cursor.get(conn, "c2") == 0 and len(pending) == 0


def test_the_cursor_connection_waits_a_second_for_a_lock_not_five(tmp_path, monkeypatch):
    monkeypatch.setenv("SLIMM_CURSOR_DB", str(tmp_path / "cursor.db"))
    bot = Bot()
    bot._open_cursor()
    waited = []
    real_connect = sqlite3.connect
    bot._cursor_conn.close()
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **kw: waited.append(kw.get("timeout")) or real_connect(*a, **kw))
    bot._open_cursor()
    assert waited == [botmod.CURSOR_BUSY_SECONDS] and botmod.CURSOR_BUSY_SECONDS < 5.0
