"""A command message deleted before its answer: the private answer is dropped, never posted in the channel instead."""

import pytest

from slimbots import Bot
from slimbots.authors import AuthorFilter
from slimbots.exceptions import CommandError
from slimbots.http import ApiError
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient


@pytest.fixture
async def rig():
    client = FakeAsyncClient(me_id="bot-1")
    bot = Bot(prefix="!")
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space)
    bot.me_id = "bot-1"
    client.respond("GET", "/members", [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}])
    await bot.space.refresh_members()
    client.respond("POST", "/channels/c1/ephemeral-messages", ApiError(404, {"error": "message not found"}))
    errors = []

    @bot.event
    async def on_command_error(ctx, err):
        errors.append(err)

    @bot.command(name="secret")
    async def secret(ctx):
        await ctx.reply_ephemeral("your private list", public_fallback=True)

    @bot.command(name="refuse")
    async def refuse(ctx):
        raise CommandError("not here")

    return bot, client, errors


async def say(bot, text):
    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": text})


async def test_a_private_answer_to_a_deleted_command_is_dropped_and_reported(rig):
    bot, client, errors = rig
    await say(bot, "!secret")
    assert client.sent == []
    assert [type(e) for e in errors] == [ApiError]


async def test_a_refusal_to_a_deleted_command_is_dropped_and_reported(rig):
    bot, client, errors = rig
    await say(bot, "!refuse")
    assert client.sent == []
    assert [type(e) for e in errors] == [CommandError]
