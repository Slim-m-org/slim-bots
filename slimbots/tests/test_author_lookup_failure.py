"""A command whose author cannot be looked up is logged when the lookup failed, and a revoked token still stops the bot."""

import logging

import pytest

from slimbots import Bot
from slimbots.authors import AuthorFilter
from slimbots.http import ApiError
from slimbots.lifecycle import guard_dispatch
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

MESSAGE = {"id": "m1", "author_id": "stranger", "channel_id": "c1", "content": "!ping"}


def a_bot():
    client = FakeAsyncClient(me_id="bot-1")
    bot = Bot(prefix="!", ignore_bots=False)
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space)
    ran = []

    @bot.command()
    async def ping(ctx):
        ran.append(ctx.author.id)

    return bot, client, ran


async def test_a_command_from_an_author_the_server_failed_to_look_up_is_logged_not_dropped_silently(caplog):
    bot, client, ran = a_bot()
    client.respond("GET", "/users/stranger", ApiError(503, {"error": "unavailable"}))
    with caplog.at_level(logging.WARNING):
        await guard_dispatch(bot.process_message, MESSAGE)
    assert ran == []
    assert any("process_message" in record.getMessage() and "HTTP 503" in record.getMessage() for record in caplog.records)


async def test_a_revoked_token_during_the_author_lookup_reaches_the_caller():
    bot, client, ran = a_bot()
    client.respond("GET", "/users/stranger", ApiError(401, {"error": "unauthorized"}))
    with pytest.raises(ApiError) as raised:
        await guard_dispatch(bot.process_message, MESSAGE)
    assert raised.value.status == 401


async def test_an_author_the_server_does_not_know_is_a_quiet_drop(caplog):
    bot, client, ran = a_bot()
    client.respond("GET", "/users/stranger", ApiError(404, {"error": "not found"}))
    with caplog.at_level(logging.WARNING):
        await guard_dispatch(bot.process_message, MESSAGE)
    assert ran == [] and caplog.records == []
