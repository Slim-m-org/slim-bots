from slimbots import Bot
from slimbots.http import ApiError
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

CHANNELS = [
    {"id": "v1", "name": "lounge", "kind": "voice"},
    {"id": "v2", "name": "games", "kind": "voice"},
    {"id": "t1", "name": "chat", "kind": "text"},
]


async def make(rosters=None):
    """A bot whose space holds two voice channels and one text channel; `rosters` maps a channel id to its voice roster."""
    client = FakeAsyncClient()
    client.respond("GET", "/channels", CHANNELS)
    for channel_id, roster in (rosters or {}).items():
        client.respond("GET", f"/channels/{channel_id}/voice/roster", roster)
    bot = Bot()
    bot.client, bot.space = client, Space(client)
    await bot.space.refresh_channels()
    return bot, client


def roster_calls(client):
    return [path for method, path, _, _ in client.calls if path.endswith("/voice/roster")]


def roster_of(*user_ids):
    return {"participants": [{"user_id": user_id} for user_id in user_ids]}


async def test_a_joined_frame_answers_find_member_without_asking_the_server():
    bot, client = await make()
    await bot._handle_frame({"type": "voice.participant_joined", "channel_id": "v1", "user_id": "u1"})
    assert await bot.voice.find_member("u1") == "v1"
    assert roster_calls(client) == []


async def test_a_left_frame_evicts_the_member_so_the_roster_is_asked_again():
    bot, client = await make({"v1": roster_of(), "v2": roster_of()})
    await bot._handle_frame({"type": "voice.participant_joined", "channel_id": "v1", "user_id": "u1"})
    await bot._handle_frame({"type": "voice.participant_left", "channel_id": "v1", "user_id": "u1"})
    assert await bot.voice.find_member("u1") is None
    assert sorted(roster_calls(client)) == ["/channels/v1/voice/roster", "/channels/v2/voice/roster"]


async def test_a_left_frame_for_another_channel_does_not_evict():
    bot, client = await make()
    await bot._handle_frame({"type": "voice.participant_joined", "channel_id": "v2", "user_id": "u1"})
    await bot._handle_frame({"type": "voice.participant_left", "channel_id": "v1", "user_id": "u1"})
    assert await bot.voice.find_member("u1") == "v2"
    assert roster_calls(client) == []


async def test_moving_between_calls_leaves_the_member_in_the_new_one_whichever_frame_arrives_first():
    for frames in (("joined", "left"), ("left", "joined")):
        bot, _ = await make()
        await bot._handle_frame({"type": "voice.participant_joined", "channel_id": "v1", "user_id": "u1"})
        for kind in frames:
            channel_id = "v2" if kind == "joined" else "v1"
            await bot._handle_frame({"type": f"voice.participant_{kind}", "channel_id": channel_id, "user_id": "u1"})
        assert await bot.voice.find_member("u1") == "v2", frames


async def test_the_roster_fallback_finds_a_member_the_bot_never_saw_join_and_remembers_it():
    bot, client = await make({"v1": roster_of("other"), "v2": roster_of("u1")})
    assert await bot.voice.find_member("u1") == "v2"
    calls_after_first = len(roster_calls(client))
    assert await bot.voice.find_member("u1") == "v2"
    assert len(roster_calls(client)) == calls_after_first


async def test_the_roster_fallback_only_asks_voice_channels():
    bot, client = await make({"v1": roster_of(), "v2": roster_of()})
    await bot.voice.find_member("u1")
    assert "/channels/t1/voice/roster" not in roster_calls(client)


async def test_one_channels_roster_failing_does_not_hide_the_member_in_another():
    bot, _ = await make({"v1": ApiError(403, {"error": "forbidden"}), "v2": roster_of("u1")})
    assert await bot.voice.find_member("u1") == "v2"


async def test_a_member_in_no_call_is_none_and_is_not_cached():
    bot, client = await make({"v1": roster_of(), "v2": roster_of()})
    assert await bot.voice.find_member("u1") is None
    assert await bot.voice.find_member("u1") is None
    assert len(roster_calls(client)) == 4


async def test_voice_frames_are_tracked_for_every_channel_whatever_the_bot_is_scoped_to():
    bot, _ = await make()
    bot.channels = {"t1"}
    await bot._handle_frame({"type": "voice.participant_joined", "channel_id": "v1", "user_id": "u1"})
    assert await bot.voice.find_member("u1") == "v1"
