import asyncio
import time

from slimbots import cursor
from test_catchup_order import FakeServer, make_bot, run_connection


def create_aged(server, seq, content, age_seconds):
    server.create(seq, content)
    server.log[-1]["created_at"] = int((time.time() - age_seconds) * 1000)


def stop_when_idle(gateways):
    async def stopper():
        await asyncio.sleep(0.2)
        gateways[0].queue.put_nowait(None)

    return asyncio.create_task(stopper())


async def test_a_command_typed_while_the_bot_was_down_is_not_run_and_the_cursor_moves_past_it(monkeypatch):
    server = FakeServer()
    create_aged(server, 1, "!bet 100", 3600)
    create_aged(server, 2, "!bet 100", 5)
    bot, client, gateways = make_bot(monkeypatch, server)
    ran = []

    @bot.command(name="bet")
    async def bet(ctx):
        ran.append(ctx.message["seq"])

    stopper = stop_when_idle(gateways)
    await run_connection(bot)
    await stopper
    assert ran == [2]
    assert cursor.get(bot._cursor_conn, "c1") == 2


async def test_a_stale_plain_message_still_reaches_on_message(monkeypatch):
    server = FakeServer()
    create_aged(server, 1, "hello there", 3600)
    bot, _client, gateways = make_bot(monkeypatch, server)
    seen = []

    @bot.event
    async def on_message(message):
        seen.append(message["seq"])

    stopper = stop_when_idle(gateways)
    await run_connection(bot)
    await stopper
    assert seen == [1]


async def test_max_replay_age_none_replays_every_command(monkeypatch):
    server = FakeServer()
    create_aged(server, 1, "!bet 100", 3600)
    bot, _client, gateways = make_bot(monkeypatch, server)
    bot.max_replay_age = None
    ran = []

    @bot.command(name="bet")
    async def bet(ctx):
        ran.append(ctx.message["seq"])

    stopper = stop_when_idle(gateways)
    await run_connection(bot)
    await stopper
    assert ran == [1]


async def test_an_unscoped_bot_catches_up_every_visible_channel_like_a_scoped_one(monkeypatch):
    server = FakeServer()
    create_aged(server, 1, "!bet 100", 3600)
    create_aged(server, 2, "!bet 100", 5)
    bot, client, gateways = make_bot(monkeypatch, server)
    client.respond("GET", "/channels", [{"id": "c1", "name": "general", "kind": "text"}])
    bot.channels = None
    ran = []

    @bot.command(name="bet")
    async def bet(ctx):
        ran.append(ctx.message["seq"])

    stopper = stop_when_idle(gateways)
    await run_connection(bot)
    await stopper
    assert ran == [2]
    assert cursor.get(bot._cursor_conn, "c1") == 2


async def test_a_replayed_command_knows_it_is_a_replay_and_a_live_one_does_not(monkeypatch):
    server = FakeServer()
    create_aged(server, 1, "!bet 100", 5)
    bot, _client, gateways = make_bot(monkeypatch, server)
    seen = []

    @bot.command(name="bet")
    async def bet(ctx):
        seen.append((ctx.message["seq"], ctx.replayed))

    @bot.event
    async def on_ready():
        create_aged(server, 2, "!bet 100", 0)

    stopper = stop_when_idle(gateways)
    await run_connection(bot)
    await stopper
    assert seen == [(1, True), (2, False)]


async def test_replay_never_feeds_the_live_only_raw_message_event(monkeypatch):
    server = FakeServer()
    create_aged(server, 1, "spam spam", 5)
    bot, _client, gateways = make_bot(monkeypatch, server)
    raw = []

    @bot.event
    async def on_raw_message(message):
        raw.append(message["seq"])

    stopper = stop_when_idle(gateways)
    await run_connection(bot)
    await stopper
    assert raw == [], "automod listens here, so a replay cannot delete or warn twice"
