"""Frame handlers run as their own tasks, so one slow handler never stalls the single frame loop behind it."""

import asyncio
import logging

import pytest

from slimbots import Bot
from slimbots.authors import AuthorFilter
from slimbots.http import ApiError
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

MEMBER = {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}


def message_frame(content, msg_id):
    return {"type": "message.created", "channel_id": "c1", "message": {"id": msg_id, "author_id": "u1", "channel_id": "c1", "content": content}}


async def a_bot():
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", [MEMBER])
    bot = Bot(prefix="!", channels={"c1"})
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space)
    await bot.space.refresh_members()
    return bot, client


async def finish(bot):
    await asyncio.gather(*list(bot._background_tasks), return_exceptions=True)


async def test_a_slow_on_raw_message_does_not_hold_up_the_next_frame():
    bot, _client = await a_bot()
    release, started = asyncio.Event(), []

    @bot.event
    async def on_raw_message(message):
        started.append(message["id"])
        if message["id"] == "m1":
            await release.wait()

    await asyncio.wait_for(bot._handle_frame(message_frame("slow", "m1")), 1)
    await asyncio.wait_for(bot._handle_frame(message_frame("quick", "m2")), 1)
    await asyncio.sleep(0.01)
    assert started == ["m1", "m2"], "the second frame waited for the first handler"
    release.set()
    await finish(bot)


@pytest.mark.parametrize("frame", [
    {"type": "member.removed", "user_id": "u1"},
    {"type": "reactions.changed", "channel_id": "c1", "message_id": "m1", "reactions": []},
    {"type": "member.joined", "user_id": "u1"},
], ids=["raw-global", "typed-channel", "member-joined"])
async def test_a_slow_handler_for_any_event_does_not_hold_up_the_next_frame(frame):
    bot, _client = await a_bot()
    release, reached = asyncio.Event(), []

    async def slow(*_args):
        reached.append("slow")
        await release.wait()

    for name in ("on_member_removed", "on_reactions_changed", "on_member_join"):
        bot.event(slow, name=name)

    await asyncio.wait_for(bot._handle_frame(frame), 1)
    await asyncio.wait_for(bot._handle_frame(message_frame("after", "m2")), 1)
    await asyncio.sleep(0.01)
    assert reached == ["slow"]
    release.set()
    await finish(bot)


async def test_a_handler_can_wait_for_a_later_frame_without_deadlocking_the_loop():
    bot, _client = await a_bot()
    got = []

    @bot.event
    async def on_raw_message(message):
        if message["content"] == "ask":
            reply = await bot.wait_for("on_raw_message", check=lambda m: m["content"] == "answer", timeout=2)
            got.append(reply["id"])

    await asyncio.wait_for(bot._handle_frame(message_frame("ask", "m1")), 1)
    await asyncio.sleep(0.01)
    await asyncio.wait_for(bot._handle_frame(message_frame("answer", "m2")), 1)
    await asyncio.sleep(0.01)
    assert got == ["m2"]
    await finish(bot)


async def test_handlers_start_in_frame_arrival_order():
    bot, _client = await a_bot()
    order = []

    @bot.event
    async def on_raw_message(message):
        order.append(message["id"])
        await asyncio.sleep(0.05 if message["id"] == "m1" else 0)

    for number in range(1, 5):
        await bot._handle_frame(message_frame("x", f"m{number}"))
    await finish(bot)
    assert order == ["m1", "m2", "m3", "m4"]


async def test_a_frame_nobody_listens_for_starts_no_task():
    bot, _client = await a_bot()
    await bot._handle_frame({"type": "presence.changed", "user_id": "u1", "status": "online"})
    await bot._handle_frame({"type": "some_future_event"})
    assert bot._background_tasks == set()


async def test_a_failing_handler_is_logged_and_the_bot_keeps_serving_frames(caplog):
    bot, _client = await a_bot()
    seen = []

    @bot.event
    async def on_member_removed(frame):
        seen.append(frame["user_id"])
        if frame["user_id"] == "bad":
            raise RuntimeError("handler bug")

    with caplog.at_level(logging.ERROR):
        for user in ("bad", "good"):
            await bot._handle_frame({"type": "member.removed", "user_id": user})
        await finish(bot)
    assert seen == ["bad", "good"]
    assert bot._fatal_error is None
    assert any(r.exc_info and "handler bug" in str(r.exc_info[1]) for r in caplog.records)


async def test_a_listener_that_leaves_mid_dispatch_does_not_make_the_next_one_skip():
    bot, _client = await a_bot()
    ran = []

    async def leaves(*_args):
        ran.append("leaves")
        bot._listeners["on_custom"].remove(leaves)

    async def stays(*_args):
        ran.append("stays")

    bot.event(leaves, name="on_custom")
    bot.event(stays, name="on_custom")
    await bot._dispatch_event("on_custom")
    assert ran == ["leaves", "stays"]


async def test_a_member_join_whose_lookup_fails_is_logged_not_dropped_silently(caplog):
    bot, client = await a_bot()
    greeted = []

    @bot.event
    async def on_member_join(member):
        greeted.append(member.id)

    client.respond("GET", "/users/newbie", ApiError(503, {"error": "unavailable"}))
    with caplog.at_level(logging.WARNING):
        await bot._handle_frame({"type": "member.joined", "user_id": "newbie"})
        await finish(bot)
    assert greeted == []
    assert any("HTTP 503" in r.getMessage() for r in caplog.records)
    assert bot._fatal_error is None
