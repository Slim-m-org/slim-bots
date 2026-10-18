"""An exception a command does not handle is logged with its traceback and answered with one generic line."""

import logging

import pytest

from slimbots import Bot
from slimbots.authors import AuthorFilter
from slimbots.bot import UNEXPECTED_ERROR_REPLY
from slimbots.http import ApiError
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

SECRET = "token=hunter2 at /srv/private/path"


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

    @bot.command(name="boom")
    async def boom(ctx):
        raise RuntimeError(SECRET)

    return bot, client


async def run(bot):
    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!boom"})


async def test_a_crashing_command_answers_privately_without_the_exception_text(rig):
    bot, client = rig
    await run(bot)
    assert client.ephemerals[-1]["content"] == UNEXPECTED_ERROR_REPLY
    assert client.sent == []
    assert "hunter2" not in str(client.ephemerals) + str(client.sent)


async def test_the_traceback_goes_to_the_log(rig, caplog):
    bot, _ = rig
    with caplog.at_level(logging.ERROR, logger="slimbots.bot"):
        await run(bot)
    record = next(r for r in caplog.records if "unhandled error in command `boom`" in r.getMessage())
    assert record.exc_info and SECRET in str(record.exc_info[1])


async def test_an_old_server_without_private_replies_gets_the_generic_line_in_the_channel(rig):
    bot, client = rig
    client.respond("POST", "/channels/c1/ephemeral-messages", ApiError(404, {"error": "not found"}))
    await run(bot)
    assert client.sent[-1]["content"] == UNEXPECTED_ERROR_REPLY


async def test_a_failing_apology_neither_raises_nor_skips_on_command_error(rig):
    bot, client = rig
    seen = []

    @bot.event
    async def on_command_error(ctx, err):
        seen.append(err)

    client.respond("POST", "/channels/c1/ephemeral-messages", ApiError(500, {"error": "down"}))
    await run(bot)
    assert len(seen) == 1 and isinstance(seen[0], RuntimeError)
