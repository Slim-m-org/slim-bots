"""`space.members` follows the removal, restore and profile frames instead of waiting for the next reconnect."""

import logging

from slimbots import Bot, Permissions
from slimbots.authors import AuthorFilter
from slimbots.http import ApiError
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

ROLES = [
    {"id": "everyone", "name": "@everyone", "permissions": 0, "is_everyone": True},
    {"id": "approver", "name": "Approver", "permissions": Permissions.MANAGE_SERVER, "is_everyone": False},
]


def person(username="nick", role_ids=()):
    return {"id": "u1", "username": username, "display_name": username.title(), "is_bot": False, "is_webhook": False, "role_ids": list(role_ids)}


async def a_bot(*cached_role_ids):
    client = FakeAsyncClient(me_id="bot-1")
    bot = Bot(channels={"c1"})
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space)
    client.respond("GET", "/roles", ROLES)
    client.respond("GET", "/members", [person(role_ids=cached_role_ids)])
    await bot.space.refresh_roles()
    await bot.space.refresh_members()
    return bot, client


async def test_a_renamed_member_resolves_by_the_new_name_and_not_the_old_one():
    bot, client = await a_bot()
    client.respond("GET", "/users/u1", person("renamed"))
    await bot._handle_frame({"type": "profile.changed", "user_id": "u1"})
    assert (await bot.space.get_member("renamed")).id == "u1"
    assert await bot.space.get_member("nick") is None


async def test_a_removed_member_leaves_the_cache_and_loses_their_permissions():
    bot, _client = await a_bot("approver")
    assert bot.space.members["u1"].has_permission(Permissions.MANAGE_SERVER)
    await bot._handle_frame({"type": "member.removed", "user_id": "u1"})
    assert "u1" not in bot.space.members
    assert await bot.space.get_member("nick") is None


async def test_a_restored_member_is_cached_again_with_the_roles_they_have_now():
    bot, client = await a_bot("approver")
    await bot._handle_frame({"type": "member.removed", "user_id": "u1"})
    client.respond("GET", "/users/u1", person(role_ids=[]))
    await bot._handle_frame({"type": "member.restored", "user_id": "u1"})
    restored = bot.space.members["u1"]
    assert not restored.has_permission(Permissions.MANAGE_SERVER)


async def test_a_profile_change_for_a_member_not_in_the_cache_costs_no_request():
    bot, client = await a_bot()
    before = len(client.calls)
    await bot._handle_frame({"type": "profile.changed", "user_id": "stranger"})
    assert len(client.calls) == before


async def test_a_failed_refetch_is_logged_and_keeps_serving_the_old_entry(caplog):
    bot, client = await a_bot()
    client.respond("GET", "/users/u1", ApiError(503, {"error": "unavailable"}))
    with caplog.at_level(logging.WARNING):
        await bot._handle_frame({"type": "profile.changed", "user_id": "u1"})
    assert bot.space.members["u1"].username == "nick"
    assert any("HTTP 503" in record.getMessage() for record in caplog.records)
