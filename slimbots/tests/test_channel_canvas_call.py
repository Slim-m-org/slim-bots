"""`Channel.has_canvas` and `Channel.hosts_call`: the server's two flags, and the pre-flag rule when they are absent."""

import pytest
from test_bot_scoping import frame, make_bot, record_raw_messages
from test_voice_find_member import make, roster_of

from slimbots import Bot
from slimbots.models import Channel
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

# (kind, flags, parent_message_id) -> (has_canvas, hosts_call)
TABLE = [
    ("text", {}, None, False, False),
    ("text", {"canvas_enabled": False, "voice_enabled": False}, None, False, False),
    ("voice", {}, None, True, True),
    ("voice", {"canvas_enabled": True, "voice_enabled": True}, None, True, True),
    ("voice", {"canvas_enabled": False, "voice_enabled": True}, None, False, True),
    ("canvas", {}, None, False, False),
    ("canvas", {"canvas_enabled": True, "voice_enabled": True}, None, True, True),
    ("canvas", {"canvas_enabled": True, "voice_enabled": False}, None, True, False),
    ("dm", {}, None, True, False),
    ("dm", {"canvas_enabled": True, "voice_enabled": True}, None, True, False),
    ("text", {"canvas_enabled": True, "voice_enabled": True}, "m1", False, False),
    ("voice", {"canvas_enabled": True, "voice_enabled": True}, "m1", False, False),
    ("whiteboard", {}, None, False, False),
]


@pytest.mark.parametrize(("kind", "flags", "parent", "canvas", "call"), TABLE)
def test_the_two_answers_for_every_kind_and_flag_combination(kind, flags, parent, canvas, call):
    data = {"id": "c", "name": "x", "kind": kind, **flags}
    if parent:
        data["parent_message_id"] = parent
    channel = Channel(data)
    assert (channel.has_canvas, channel.hosts_call) == (canvas, call)


def test_absent_flags_read_as_none_and_present_ones_as_given():
    assert Channel({"id": "c", "name": "x", "kind": "voice"}).canvas_enabled is None
    channel = Channel({"id": "c", "name": "x", "kind": "voice", "canvas_enabled": False, "voice_enabled": True})
    assert (channel.canvas_enabled, channel.voice_enabled) == (False, True)


def test_a_non_boolean_flag_is_treated_as_absent():
    assert Channel({"id": "c", "name": "x", "kind": "voice", "canvas_enabled": "no"}).has_canvas is True


def test_a_channel_frame_with_an_unknown_kind_builds_without_error():
    channel = Channel({"id": "c", "name": "x", "kind": "stage", "canvas_enabled": None})
    assert (channel.has_canvas, channel.hosts_call) == (False, False)


@pytest.mark.parametrize("flags", [{}, {"voice_enabled": True, "canvas_enabled": True}])
async def test_find_member_finds_a_call_in_a_canvas_channel_that_hosts_one(flags):
    bot, client = await make()
    bot.space.channels["k1"] = Channel({"id": "k1", "name": "design", "kind": "canvas", **flags})
    client.respond("GET", "/channels/k1/voice/roster", roster_of("u1"))
    client.respond("GET", "/channels/v1/voice/roster", roster_of())
    client.respond("GET", "/channels/v2/voice/roster", roster_of())
    found = await bot.voice.find_member("u1")
    assert found == ("k1" if flags else None)


async def test_find_member_never_asks_a_canvas_channel_without_a_call():
    bot, client = await make({"v1": roster_of(), "v2": roster_of()})
    bot.space.channels["k1"] = Channel({"id": "k1", "name": "design", "kind": "canvas", "canvas_enabled": True, "voice_enabled": False})
    assert await bot.voice.find_member("u1") is None
    assert "/channels/k1/voice/roster" not in [path for _, path, _, _ in client.calls]


async def test_find_member_skips_a_voice_channel_whose_call_flag_says_no():
    bot, client = await make({"v1": roster_of(), "v2": roster_of()})
    bot.space.channels["v1"] = Channel({"id": "v1", "name": "lounge", "kind": "voice", "voice_enabled": False})
    await bot.voice.find_member("u1")
    assert "/channels/v1/voice/roster" not in [path for _, path, _, _ in client.calls]


@pytest.mark.parametrize(("flags", "heard"), [({"voice_enabled": True}, ["k"]), ({"voice_enabled": False}, []), ({}, [])])
async def test_listen_voice_chats_hears_a_canvas_channel_only_when_it_hosts_a_call(monkeypatch, flags, heard):
    bot, _ = await make_bot(monkeypatch, channels={"text"}, listen_voice_chats=True)
    bot.space.channels["k"] = Channel({"id": "k", "name": "design", "kind": "canvas", **flags})
    seen = record_raw_messages(bot)
    from test_bot_scoping import feed_frame

    await feed_frame(bot, frame("k"))
    assert seen == heard
