import asyncio
import sqlite3

from slimbots import Bot, cursor
from slimbots.authors import AuthorFilter
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

PER_SCOPE_LIMIT = 100
SNAPSHOT_GAP = 1000


class ServerLikeClient(FakeAsyncClient):
    """POST /sync answered the way crates/slimm-server/src/http/sync.rs does: a page cap per scope, a shared budget, reset when far behind."""

    def __init__(self, latest, aggregate_limit=500):
        super().__init__(me_id="bot-1")
        self.latest = latest
        self.aggregate_limit = aggregate_limit
        self.sync_calls = 0

    async def call(self, method, path, body=None, **kw):
        if (method, path) == ("POST", "/sync"):
            self.sync_calls += 1
            assert self.sync_calls < 20, "catch-up keeps asking for a page that never comes"
            return {"scopes": self._scopes(body["scopes"])}
        if method == "GET" and path.endswith("/messages?limit=1"):
            channel_id = path.split("/")[2]
            return [self._row(channel_id, self.latest[channel_id])]
        return await super().call(method, path, body, **kw)

    @staticmethod
    def _row(channel_id, seq):
        return {"id": f"{channel_id}-m{seq}", "author_id": "u1", "channel_id": channel_id, "content": "x", "seq": seq}

    def _scopes(self, requested):
        budget = self.aggregate_limit
        scopes = []
        for scope in requested:
            channel_id, after = scope["channel_id"], scope["after_seq"]
            latest = self.latest[channel_id]
            messages, has_more, reset = [], False, False
            if after >= latest:
                pass
            elif latest - after > SNAPSHOT_GAP:
                has_more, reset = True, True
            else:
                limit = min(PER_SCOPE_LIMIT, budget)
                rows = [self._row(channel_id, n) for n in range(after + 1, latest + 1)]
                messages, has_more = rows[:limit], len(rows) > limit
                budget -= len(messages)
            scopes.append({"channel_id": channel_id, "messages": messages, "has_more": has_more, "reset": reset, "ops": []})
        return scopes


def make_bot(monkeypatch, tmp_path, client, channels):
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    client.respond("GET", "/users/u1", {"id": "u1", "username": "u", "is_bot": False})
    bot = Bot(channels=set(channels), cursor_path=str(tmp_path / "cursor.db"))
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space)
    bot.me_id = "bot-1"
    bot._cursor_conn = sqlite3.connect(str(tmp_path / "cursor.db"), isolation_level=None)
    cursor.init_table(bot._cursor_conn)
    for channel in channels:
        cursor.set(bot._cursor_conn, channel, 0)
    return bot


def record_messages(bot):
    seen = []

    @bot.event
    async def on_message(message):
        seen.append((message["channel_id"], message["seq"]))

    return seen


async def test_every_message_missed_while_offline_is_delivered_past_the_first_page(monkeypatch, tmp_path):
    client = ServerLikeClient(latest={"c1": 120})
    bot = make_bot(monkeypatch, tmp_path, client, ["c1"])
    seen = record_messages(bot)
    await bot._catch_up()
    await bot._handle_frame({"type": "message.created", "channel_id": "c1", "message": ServerLikeClient._row("c1", 121)})
    await asyncio.sleep(0.05)
    missing = sorted(set(range(1, 122)) - {seq for _, seq in seen})
    assert not missing, f"never delivered: seqs {missing[0]}..{missing[-1]} ({len(missing)} messages)"
    assert [seq for _, seq in seen if seq <= 120] == list(range(1, 121))


async def test_a_busy_scope_does_not_starve_the_scopes_after_it_of_the_shared_budget(monkeypatch, tmp_path):
    client = ServerLikeClient(latest={"c1": 400, "c2": 300}, aggregate_limit=250)
    bot = make_bot(monkeypatch, tmp_path, client, ["c1", "c2"])
    seen = record_messages(bot)
    await bot._catch_up()
    await asyncio.sleep(0.05)
    assert sorted(seq for channel, seq in seen if channel == "c1") == list(range(1, 401))
    assert sorted(seq for channel, seq in seen if channel == "c2") == list(range(1, 301))


async def test_a_cursor_too_far_behind_to_replay_jumps_to_the_channels_newest_message(monkeypatch, tmp_path):
    client = ServerLikeClient(latest={"c1": 5000})
    bot = make_bot(monkeypatch, tmp_path, client, ["c1"])
    seen = record_messages(bot)
    await bot._catch_up()
    assert cursor.get(bot._cursor_conn, "c1") == 5000
    assert seen == []
    assert client.sync_calls == 1


async def test_catch_up_stops_when_the_server_reports_more_but_returns_nothing(monkeypatch, tmp_path):
    client = ServerLikeClient(latest={"c1": 50}, aggregate_limit=0)
    bot = make_bot(monkeypatch, tmp_path, client, ["c1"])
    record_messages(bot)
    await asyncio.wait_for(bot._catch_up(), timeout=2)
    assert client.sync_calls == 1
