"""Dropped-command header, registration validation, per-channel bootstrap, chunked /sync and menu scope (audit 24, lane 4)."""

import asyncio
import logging
import sqlite3

from slimbots import Bot, cursor
from slimbots.authors import AuthorFilter
from slimbots.catchup import MAX_SYNC_SCOPES
from slimbots.http import ApiError
from slimbots.registration import registration_body, register_commands
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient


def entry_for(bot, name):
    return next(e for e in registration_body("!", bot.unique_commands())["commands"] if e["name"] == name)


def make_bot(prefix="!", **kwargs):
    client = FakeAsyncClient()
    bot = Bot(prefix=prefix, **kwargs)
    bot.client, bot.space, bot.me_id, bot.username = client, Space(client), "bot-1", "testbot"
    bot.authors = AuthorFilter(client, space=bot.space)
    client.respond("GET", "/users/u1", {"id": "u1", "username": "u", "display_name": "U", "is_bot": False, "role_ids": []})
    return bot, client


def message(content, channel="c1"):
    return {"id": "m1", "author_id": "u1", "channel_id": channel, "content": content, "seq": 1}


async def test_a_dropped_command_is_logged_and_not_dispatched_by_prefix(caplog):
    bot, client = make_bot()
    ran = []

    @bot.command()
    async def watch(ctx):
        ran.append("watch")

    @bot.command()
    async def keep(ctx):
        ran.append("keep")

    client.respond_headers("PUT", "/bots/commands", {"x-slimm-dropped-commands": "Watch, other"})
    with caplog.at_level(logging.WARNING):
        await bot._register_commands()
    assert any("watch" in r.message.lower() and "another bot" in r.message for r in caplog.records)

    await bot.process_message(message("!watch"))
    await bot.process_message(message("!keep"))
    await bot.process_message(message("@testbot watch"))
    assert ran == ["keep", "watch"]


async def test_a_reregistration_without_the_header_clears_the_dropped_set():
    bot, client = make_bot()

    @bot.command()
    async def watch(ctx):
        pass

    client.respond_headers("PUT", "/bots/commands", {"x-slimm-dropped-commands": "watch"})
    await bot._register_commands()
    client.respond_headers("PUT", "/bots/commands", {})
    await bot._register_commands()
    assert bot._dropped_commands == set()


async def test_the_dropped_header_reaches_a_register_commands_caller():
    client = FakeAsyncClient()
    client.respond_headers("PUT", "/bots/commands", {"x-slimm-dropped-commands": "a,b"})
    got = []
    await register_commands(client, prefix="!", commands=[], on_dropped=got.extend)
    assert got == ["a", "b"]


def test_a_help_string_with_newlines_and_hidden_characters_registers_clean():
    bot = Bot(prefix="!")

    @bot.command(help="first line\nsecond\tline\u200b with a\u202e mark")
    async def ping(ctx):
        pass

    entry = entry_for(bot, "ping")
    assert entry["description"] == "first line second line with a mark"


def test_usage_is_cleaned_and_an_all_hidden_help_falls_back_to_the_name():
    bot = Bot(prefix="!")

    @bot.command(help="\u200b\n", usage="<a>\n<b>")
    async def ping(ctx):
        pass

    entry = entry_for(bot, "ping")
    assert entry["description"] == "ping"
    assert entry["usage"] == "<a> <b>"


def test_names_the_server_would_refuse_are_left_out_not_fatal(caplog):
    bot = Bot(prefix="!")

    @bot.command(aliases=("café", "x" * 33, "p ing"))
    async def ping(ctx):
        pass

    with caplog.at_level(logging.WARNING):
        names = [e["name"] for e in registration_body("!", bot.unique_commands())["commands"]]
    assert names == ["help", "ping"]
    assert len([r for r in caplog.records if "not registered" in r.message]) == 3


def test_add_command_rejects_a_name_that_differs_only_by_case():
    bot = Bot(prefix="!")

    @bot.command()
    async def ping(ctx):
        pass

    try:
        @bot.command(name="Ping")
        async def other(ctx):
            pass
    except ValueError as err:
        assert "case" in str(err)
    else:
        raise AssertionError("a case-only duplicate must be rejected")


def catch_up_bot(tmp_path, client, channels):
    bot = Bot(channels=set(channels) if channels else None, cursor_path=str(tmp_path / "c.db"))
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space)
    bot._cursor_conn = sqlite3.connect(str(tmp_path / "c.db"), isolation_level=None)
    cursor.init_table(bot._cursor_conn)
    return bot


class SyncClient(FakeAsyncClient):
    def __init__(self, refuse=None):
        super().__init__()
        self.refuse = refuse or {}
        self.sync_sizes = []

    async def call(self, method, path, body=None, **kw):
        if method == "GET" and path.endswith("/messages?limit=1"):
            err = self.refuse.get(path.split("/")[2])
            if err:
                raise err
            return []
        if (method, path) == ("POST", "/sync"):
            scopes = body["scopes"]
            self.sync_sizes.append(len(scopes))
            if len(scopes) > 200:
                raise ApiError(400, {"error": "too many scopes"})
            return {"scopes": [{"channel_id": s["channel_id"], "messages": [], "has_more": False, "reset": False, "ops": []} for s in scopes]}
        return await super().call(method, path, body, **kw)


async def test_a_bot_with_250_visible_channels_finishes_catch_up(tmp_path):
    client = SyncClient()
    bot = catch_up_bot(tmp_path, client, None)
    client.respond("GET", "/channels", [{"id": f"c{i:03}", "name": f"n{i}", "kind": "text"} for i in range(250)])
    await bot.space.refresh_channels()
    await bot._catch_up()
    assert client.sync_sizes == [200, 50]


async def test_one_unviewable_configured_channel_is_dropped_not_fatal(tmp_path, caplog):
    client = SyncClient(refuse={"bad": ApiError(403, {"error": "forbidden"})})
    bot = catch_up_bot(tmp_path, client, ["good", "bad"])
    with caplog.at_level(logging.ERROR):
        await bot._catch_up()
    assert client.sync_sizes == [1]
    assert any("bad" in r.message for r in caplog.records if r.levelno == logging.ERROR)


async def test_a_transient_bootstrap_failure_still_raises(tmp_path):
    client = SyncClient(refuse={"c1": ApiError(503, {"error": "down"})})
    bot = catch_up_bot(tmp_path, client, ["c1"])
    try:
        await bot._catch_up()
    except ApiError as err:
        assert err.status == 503
    else:
        raise AssertionError("a 503 must still fail the catch-up")


def test_the_scope_cap_matches_the_servers():
    assert MAX_SYNC_SCOPES == 200


async def test_a_message_menu_use_outside_the_bots_channels_is_dispatched():
    bot, client = make_bot(channels={"c1"})
    got = []

    @bot.message_menu("translate", "Translate")
    async def translate(interaction):
        got.append(interaction.channel_id)

    frame = {
        "type": "interaction.created", "interaction_id": "i1", "channel_id": "other", "kind": "message_menu",
        "custom_id": "translate", "user_id": "u1", "user_display_name": "A", "created_at": 1, "message_id": "m1",
    }
    await bot._handle_frame(frame)
    await asyncio.gather(*list(bot._background_tasks))
    assert got == ["other"]


async def test_call_hands_the_response_headers_to_on_headers_on_a_204():
    import httpx
    from slimbots.http import AsyncClient

    client = AsyncClient("https://fake.invalid", "slimbot_fake", "t/1")
    client._http = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(204, headers={"x-slimm-dropped-commands": "a,b"})),
        base_url="https://fake.invalid",
    )
    seen = []
    assert await client.call("PUT", "/bots/commands", {}, on_headers=seen.append) is None
    assert seen[0]["x-slimm-dropped-commands"] == "a,b"
