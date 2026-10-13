#!/usr/bin/env python3
"""Tests against FakeAsyncClient, no server or game host needed; run directly: python3 test_bot.py."""

import asyncio
import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as game_status  # noqa: E402
import gs_core as core  # noqa: E402
import queries  # noqa: E402
from slimbots import ApiError  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

PERSON = {"id": "u-1", "username": "sam", "display_name": "Sam", "is_bot": False, "is_webhook": False}
STAMP = datetime.datetime(2026, 1, 2, 3, 4, 5, tzinfo=datetime.timezone.utc)
VALHEIM = {"name": "Valheim", "kind": "a2s", "host": "10.0.0.5", "port": 2457}
MINECRAFT = {"name": "Survival", "kind": "minecraft", "host": "10.0.0.6", "port": 25565}
FACTORIO = {"name": "Factorio", "kind": "tcp", "host": "10.0.0.7", "port": 34197}
ANSWERS = {}


async def fake_check(kind, host, port, timeout):
    return ANSWERS.get(host) or queries.down("timed out")


def accept_edits(client):
    """The fake has no message store, so an edit with no queued answer succeeds like the real server's."""
    original = client.call

    async def call(method, path, body=None, **kwargs):
        try:
            return await original(method, path, body, **kwargs)
        except KeyError:
            if method == "PATCH":
                return {}
            raise

    client.call = call


def setup(*, servers=(VALHEIM, MINECRAFT, FACTORIO)):
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/users/u-1", PERSON)
    accept_edits(client)
    bot = game_status.bot
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space)
    bot.store = None
    bot.data_path = os.path.join(tempfile.mkdtemp(), "game-status.db")
    for command in bot.commands.values():
        if command.cooldown is not None:
            command.cooldown._last.clear()
    core.GAMESTATUS_CHANNELS = []
    ANSWERS.clear()
    ANSWERS.update({
        "10.0.0.5": queries.Status(up=True, players=3, max_players=10, map="World", detail="Valheim"),
        "10.0.0.6": queries.Status(up=True, players=0, max_players=20, detail="1.21 - Hi"),
    })
    game_status.queries.check = fake_check
    asyncio.run(bot.space.refresh_members())
    store = asyncio.run(bot.open_store(migrate=core.init_db))
    for server in servers:
        asyncio.run(store.run(core.txn_upsert_server, server))
    return client


def say(client, text, *, permissions=0):
    member = game_status.bot.space._make_member({**PERSON, "role_ids": []})
    member._base_permissions = permissions
    game_status.bot.space.members["u-1"] = member
    asyncio.run(game_status.bot.process_message({"id": "m1", "channel_id": "c1", "author_id": "u-1", "content": text, "seq": 1}))


def rows_for(servers=(VALHEIM, MINECRAFT, FACTORIO)):
    return asyncio.run(game_status.poll(list(servers)))


def patches(client):
    return [call for call in client.calls if call[0] == "PATCH"]


def test_status_text_lists_up_down_players_map_and_last_checked():
    setup()
    text = core.status_text(rows_for(), STAMP)
    assert "[up] Valheim - 3/10 players - map World - Valheim" in text
    assert "[up] Survival - 0/20 players - 1.21 - Hi" in text
    assert "[down] Factorio - no answer (timed out)" in text
    assert text.endswith("last checked 03:04:05 UTC")


def test_embed_has_a_field_per_server_and_a_colour_for_mixed_state():
    setup()
    wire = core.status_embed(rows_for(), STAMP).to_wire()
    assert [f["name"] for f in wire["fields"]] == ["[up] Valheim", "[up] Survival", "[down] Factorio"]
    assert wire["description"] == "2 of 3 up" and wire["color"] == core.COLORS["mixed"]
    assert wire["footer"]["text"] == "last checked 03:04:05 UTC"


def test_embed_is_red_when_everything_is_down_and_green_when_all_up():
    setup()
    assert core.status_embed(rows_for([FACTORIO]), STAMP).to_wire()["color"] == core.COLORS["all_down"]
    assert core.status_embed(rows_for([VALHEIM]), STAMP).to_wire()["color"] == core.COLORS["all_up"]


def test_a_dead_server_does_not_stall_the_others():
    async def slow(kind, host, port, timeout):
        await asyncio.sleep(0.5) if host == "10.0.0.7" else None
        return await fake_check(kind, host, port, timeout)

    setup()
    game_status.queries.check = slow
    loop = asyncio.new_event_loop()
    started = loop.time()
    rows = loop.run_until_complete(game_status.poll([VALHEIM, FACTORIO]))
    assert loop.time() - started < 1 and rows[0][1].up


def test_gamestatus_answers_privately_with_the_embed():
    client = setup()
    say(client, "!gamestatus")
    assert not client.sent
    reply = client.ephemerals[-1]
    assert "[up] Valheim" in reply["content"] and reply["embeds"][0]["fields"][0]["name"] == "[up] Valheim"


def test_first_publish_creates_a_message_and_remembers_it():
    client = setup()
    core.GAMESTATUS_CHANNELS = ["c9"]
    asyncio.run(game_status.poll_once())
    assert len(client.sent) == 1 and client.sent[0]["channel_id"] == "c9" and "embeds" not in client.sent[0]
    assert client.sent[0]["content"].startswith("**Game servers**")
    boards = asyncio.run(game_status.bot.store.run(core.txn_list_boards))
    assert boards == {"c9": client.sent[0]["id"]}


def test_later_polls_edit_in_place_and_post_nothing_new():
    client = setup()
    core.GAMESTATUS_CHANNELS = ["c9"]
    asyncio.run(game_status.poll_once())
    asyncio.run(game_status.poll_once())
    asyncio.run(game_status.poll_once())
    assert len(client.sent) == 1
    edits = patches(client)
    assert len(edits) == 2 and all(e[1] == f"/channels/c9/messages/{client.sent[0]['id']}" for e in edits)
    assert set(edits[0][2]) == {"content"} and "[up] Valheim" in edits[0][2]["content"]


def test_a_deleted_status_message_is_recreated_and_the_new_id_stored():
    client = setup()
    core.GAMESTATUS_CHANNELS = ["c9"]
    asyncio.run(game_status.poll_once())
    old = client.sent[0]["id"]
    client.respond("PATCH", f"/channels/c9/messages/{old}", ApiError(404, "not found"))
    asyncio.run(game_status.poll_once())
    assert len(client.sent) == 2 and client.sent[1]["id"] != old
    assert asyncio.run(game_status.bot.store.run(core.txn_list_boards)) == {"c9": client.sent[1]["id"]}


def test_one_unreachable_channel_does_not_stop_the_others():
    client = setup()
    core.GAMESTATUS_CHANNELS = ["bad", "good"]
    client.respond("POST", "/channels/bad/messages", ApiError(403, "forbidden"))
    asyncio.run(game_status.poll_once())
    assert [m["channel_id"] for m in client.sent] == ["bad", "good"]
    assert asyncio.run(game_status.bot.store.run(core.txn_list_boards)).keys() == {"good"}


def test_the_poll_loop_survives_an_exception_in_a_cycle():
    setup()
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        raise asyncio.CancelledError

    game_status.poll_once = flaky
    core.GAMESTATUS_INTERVAL = 0
    try:
        asyncio.run(game_status.poll_loop())
    except asyncio.CancelledError:
        pass
    assert len(calls) == 2


def test_a_revoked_token_ends_the_poll_loop():
    setup()

    async def revoked():
        raise ApiError(401, {"error": "invalid token"})

    game_status.poll_once = revoked
    try:
        asyncio.run(game_status.poll_loop())
    except ApiError:
        return
    raise AssertionError("loop swallowed a revoked token")


def test_admin_commands_need_manage_server():
    client = setup(servers=())
    say(client, "!gameserver add tcp 10.0.0.7 34197 Factorio")
    assert "Manage Server" in client.ephemerals[-1]["content"]
    assert asyncio.run(game_status.bot.store.run(core.txn_list_servers)) == []


def test_add_list_and_remove_round_trip():
    client = setup(servers=())
    say(client, "!gameserver add tcp 10.0.0.7 34197 Factorio Space", permissions=Permissions.MANAGE_SERVER)
    say(client, "!gameserver list", permissions=Permissions.MANAGE_SERVER)
    assert "`Factorio Space` - tcp 10.0.0.7:34197" in client.sent[-1]["content"]
    say(client, "!gameserver remove factorio space", permissions=Permissions.MANAGE_SERVER)
    assert "stopped watching" in client.sent[-1]["content"]
    assert asyncio.run(game_status.bot.store.run(core.txn_list_servers)) == []


def test_add_rejects_bad_kind_host_and_port():
    client = setup(servers=())
    for line in ("!gameserver add ftp 10.0.0.7 21 X", "!gameserver add tcp bad/host 21 X", "!gameserver add tcp 10.0.0.7 70000 X"):
        say(client, line, permissions=Permissions.MANAGE_SERVER)
    assert len(client.sent) == 3
    assert asyncio.run(game_status.bot.store.run(core.txn_list_servers)) == []


def test_board_command_posts_then_unboard_stops_updates():
    client = setup()
    say(client, "!gameserver board", permissions=Permissions.MANAGE_SERVER)
    assert client.sent[0]["channel_id"] == "c1"
    say(client, "!gameserver unboard", permissions=Permissions.MANAGE_SERVER)
    assert asyncio.run(game_status.bot.store.run(core.txn_list_boards)) == {}


def test_config_entries_parse_and_bad_ones_are_reported():
    assert core.parse_server_entry("Valheim=a2s:host.example:2457")[0] == {"name": "Valheim", "kind": "a2s", "host": "host.example", "port": 2457}
    assert core.parse_server_entry("X=a2s:host")[0] is None
    assert core.parse_server_entry("X=nope:host:1")[0] is None


def test_the_interval_never_drops_below_the_minimum():
    os.environ["GAMESTATUS_INTERVAL"] = "2"
    os.environ["GAMESTATUS_MIN_INTERVAL"] = "20"
    try:
        core.configure(game_status.bot)
        assert core.GAMESTATUS_INTERVAL == 20
    finally:
        del os.environ["GAMESTATUS_INTERVAL"], os.environ["GAMESTATUS_MIN_INTERVAL"]
        core.configure(game_status.bot)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    real_poll_once, real_check = game_status.poll_once, queries.check
    for test in tests:
        game_status.poll_once, game_status.queries.check = real_poll_once, real_check
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
