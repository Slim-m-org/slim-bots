import asyncio
import sqlite3

import slimbots.bot as botmod
from slimbots import Bot, cursor
from slimbots.authors import AuthorFilter
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

MEMBER = {"id": "u1", "username": "n", "display_name": "N", "is_bot": False, "is_webhook": False, "role_ids": []}


class FakeServer:
    """A message log plus a broadcast to open sockets only, like the real hub."""

    def __init__(self):
        self.log = []
        self.sockets = []

    def create(self, seq, content=None):
        message = {"id": f"m{seq}", "seq": seq, "channel_id": "c1", "author_id": "u1", "content": content or f"msg {seq}"}
        self.log.append(message)
        for queue in self.sockets:
            queue.put_nowait({"type": "message.created", "channel_id": "c1", "message": message})


class FakeGateway:
    def __init__(self, server):
        self.hello = {}
        self.queue = asyncio.Queue()
        self.sent = []
        server.sockets.append(self.queue)

    async def send(self, frame):
        self.sent.append(frame)

    async def frames(self):
        while True:
            frame = await self.queue.get()
            if frame is None:
                return
            yield frame

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


def make_bot(monkeypatch, server, *, on_open=None, page_size=100):
    monkeypatch.delenv("SLIMM_PREFIX", raising=False)
    client = FakeAsyncClient()
    bot = Bot(channels={"c1"}, prefix="!")
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space)
    client.respond("GET", "/members", [MEMBER])
    client.respond("PUT", "/bots/commands", None)
    bot._cursor_conn = sqlite3.connect(":memory:", isolation_level=None)
    cursor.init_table(bot._cursor_conn)
    cursor.set(bot._cursor_conn, "c1", 0)

    def sync(*_args, **_kwargs):
        after = cursor.get(bot._cursor_conn, "c1")
        rest = [m for m in server.log if m["seq"] > after]
        page = rest[:page_size]
        return {"scopes": [{"channel_id": "c1", "messages": page, "has_more": len(rest) > page_size, "reset": False, "ops": []}]}

    client.respond("POST", "/sync", sync)
    gateways = []

    async def open_(_client, **_kwargs):
        gateway = FakeGateway(server)
        gateways.append(gateway)
        if on_open:
            on_open()
        return gateway

    monkeypatch.setattr(botmod.Gateway, "open", open_)
    return bot, client, gateways


async def run_connection(bot):
    try:
        await asyncio.wait_for(bot._connect_once(lambda: None), timeout=3)
    except asyncio.TimeoutError:
        pass
    while bot._background_tasks:
        await asyncio.wait(set(bot._background_tasks))
        await asyncio.sleep(0)


async def test_a_message_created_while_the_backlog_replays_is_processed(monkeypatch):
    server = FakeServer()
    server.create(1)
    bot, _client, gateways = make_bot(monkeypatch, server)
    seen = []

    async def process(message):
        seen.append(message["seq"])
        if message["seq"] == 1:
            server.create(2)
        if message["seq"] == 3:
            gateways[0].queue.put_nowait(None)

    bot.process_message = process

    @bot.event
    async def on_ready():
        server.create(3)

    await run_connection(bot)
    assert seen == [1, 2, 3], f"process_message saw {seen}"


async def test_a_live_message_during_a_paged_replay_does_not_skip_the_pages_after_it(monkeypatch):
    server = FakeServer()
    for seq in (1, 2, 3, 4):
        server.create(seq)
    bot, _client, gateways = make_bot(monkeypatch, server, page_size=1)
    seen = []

    async def process(message):
        seen.append(message["seq"])
        if message["seq"] == 1:
            server.create(5)
            await asyncio.sleep(0.05)
        if message["seq"] == 5:
            gateways[0].queue.put_nowait(None)

    bot.process_message = process
    await run_connection(bot)
    assert seen == [1, 2, 3, 4, 5], f"process_message saw {seen}"


async def test_a_message_in_both_the_backlog_and_the_live_stream_is_processed_once(monkeypatch):
    server = FakeServer()
    bot, _client, gateways = make_bot(monkeypatch, server, on_open=lambda: server.create(1))
    seen = []

    async def process(message):
        seen.append(message["seq"])
        if message["seq"] == 2:
            gateways[0].queue.put_nowait(None)

    bot.process_message = process

    @bot.event
    async def on_ready():
        server.create(2)

    await run_connection(bot)
    assert seen == [1, 2], f"process_message saw {seen}"


async def test_a_replayed_command_can_show_typing(monkeypatch):
    server = FakeServer()
    server.create(1, "!slow")
    bot, client, gateways = make_bot(monkeypatch, server)

    @bot.command(name="slow")
    async def slow(ctx):
        async with ctx.typing():
            await ctx.reply("done")
        gateways[0].queue.put_nowait(None)

    await run_connection(bot)
    assert [m["content"] for m in client.sent] == ["done"]
    assert gateways[0].sent == [{"type": "typing", "channel_id": "c1"}]


async def test_a_replayed_command_can_wait_for_the_members_reply(monkeypatch):
    server = FakeServer()
    server.create(1, "!ask")
    bot, client, gateways = make_bot(monkeypatch, server)

    @bot.command(name="ask")
    async def ask(ctx):
        await ctx.reply("confirmed" if await ctx.confirm("sure?", timeout=2) else "timed out")
        gateways[0].queue.put_nowait(None)

    async def member_answers():
        while not client.sent:
            await asyncio.sleep(0.01)
        server.create(2, "yes")

    answer = asyncio.create_task(member_answers())
    await run_connection(bot)
    await answer
    assert client.sent[-1]["content"] == "confirmed", client.sent
