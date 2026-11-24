"""`Space.permissions_in` and `Context.author_can`: a member's bits in one channel, failing closed when they cannot be read."""

import logging

from slimbots import Bot, Permissions
from slimbots.context import Context
from slimbots.http import ApiError
from slimbots.models import Member
from slimbots.space import ALL_PERMISSIONS, Space
from slimbots.testing import FakeAsyncClient

ANN = {"id": "u-ann", "username": "ann", "display_name": "Ann", "role_ids": []}
SCOPED = "/members/u-ann/permissions?channel_id=c-voice"


def make():
    client = FakeAsyncClient()
    bot = Bot(channels={"c-text"})
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    client.respond("GET", "/members/u-ann/permissions", {"user_id": "u-ann", "permissions": ALL_PERMISSIONS})
    ctx = Context(bot=bot, message={"id": "m1"}, author=Member(ANN), channel_id="c-text")
    return bot, client, ctx


def scoped(bits, *, echo=True):
    body = {"user_id": "u-ann", "permissions": bits}
    return {**body, "channel_id": "c-voice"} if echo else body


async def test_the_channel_scoped_read_names_the_channel():
    bot, client, _ = make()
    client.respond("GET", SCOPED, scoped(Permissions.VIEW_CHANNEL))
    assert await bot.space.permissions_in("u-ann", "c-voice") == Permissions.VIEW_CHANNEL
    assert ("GET", "/members/u-ann/permissions", None, {"channel_id": "c-voice"}) in client.calls


async def test_a_channel_overwrite_denying_a_bit_refuses_the_author():
    _, client, ctx = make()
    client.respond("GET", SCOPED, scoped(Permissions.VIEW_CHANNEL | Permissions.CONNECT))
    assert not await ctx.author_can(Permissions.USE_CANVAS, "c-voice")
    assert await ctx.author_can(Permissions.CONNECT, "c-voice")


async def test_an_administrator_in_the_channel_passes_every_check():
    _, client, ctx = make()
    client.respond("GET", SCOPED, scoped(Permissions.ADMINISTRATOR | Permissions.VIEW_CHANNEL))
    assert await ctx.author_can(Permissions.MANAGE_CANVAS, "c-voice")


async def test_a_failed_read_fails_closed(caplog):
    _, client, ctx = make()
    client.respond("GET", SCOPED, ApiError(500, {"error": "boom"}))
    with caplog.at_level(logging.WARNING):
        assert not await ctx.author_can(Permissions.SPEAK, "c-voice")
    assert any("could not read" in r.message for r in caplog.records)


async def test_a_malformed_answer_fails_closed():
    _, client, ctx = make()
    client.respond("GET", SCOPED, {"user_id": "u-ann"})
    assert not await ctx.author_can(Permissions.SPEAK, "c-voice")


async def test_a_revoked_token_is_not_swallowed():
    _, client, ctx = make()
    client.respond("GET", SCOPED, ApiError(401, {"error": "revoked"}))
    try:
        await ctx.author_can(Permissions.SPEAK, "c-voice")
    except ApiError as err:
        assert err.status == 401
    else:
        raise AssertionError("a revoked token must reach the bot's lifecycle handling")


async def test_an_older_server_that_ignores_the_channel_is_warned_about(caplog):
    bot, client, _ = make()
    client.respond("GET", SCOPED, scoped(Permissions.SPEAK, echo=False))
    with caplog.at_level(logging.WARNING):
        assert await bot.space.permissions_in("u-ann", "c-voice") == Permissions.SPEAK
        await bot.space.permissions_in("u-ann", "c-voice")
    assert len([r for r in caplog.records if "deployment-wide" in r.message]) == 1


async def test_no_channel_means_this_commands_own_channel():
    _, client, ctx = make()
    client.respond("GET", "/members/u-ann/permissions?channel_id=c-text", {"permissions": Permissions.SPEAK, "channel_id": "c-text"})
    assert await ctx.author_can(Permissions.SPEAK)
