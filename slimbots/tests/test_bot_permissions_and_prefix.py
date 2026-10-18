import logging

from slimbots import Bot, Permissions
from slimbots.authors import AuthorFilter
from slimbots.exceptions import MissingPermissions
from slimbots.http import ApiError
from slimbots.registration import RegistrationConflict, register_commands
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

FORBIDDEN = ApiError(403, {"error": "forbidden"})
USER = {"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}


def make(monkeypatch):
    monkeypatch.delenv("SLIMM_PREFIX", raising=False)
    client = FakeAsyncClient()
    bot = Bot(channels={"text"})
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space)
    client.respond("GET", "/roles", FORBIDDEN)
    client.respond("GET", "/members", [USER])
    return bot, client


async def test_unreadable_roles_fall_back_to_the_permissions_route(monkeypatch, caplog):
    bot, client = make(monkeypatch)
    client.respond("GET", "/members/bot-1/permissions", {"user_id": "bot-1", "permissions": 0})
    client.respond("GET", "/members/u1/permissions", {"user_id": "u1", "permissions": int(Permissions.MANAGE_SERVER)})
    with caplog.at_level(logging.WARNING):
        assert await bot._load_roles() is False
        assert await bot._load_roles() is False
    await bot.space.refresh_members()

    member = await bot._resolve_author("u1")

    assert member.has_permission(Permissions.MANAGE_SERVER)
    assert bot.permissions_readable is True
    assert len([r for r in caplog.records if "cannot read roles" in r.message]) == 1


async def test_unreadable_roles_and_no_route_is_logged_and_named_in_the_refusal(monkeypatch, caplog):
    bot, client = make(monkeypatch)
    client.respond("GET", "/members/bot-1/permissions", ApiError(404, {}))
    with caplog.at_level(logging.ERROR):
        await bot._load_roles()

    assert bot.permissions_readable is False
    assert any("requires=" in r.message for r in caplog.records)
    reply = bot._permission_refusal(MissingPermissions("manage_server"))
    assert reply is not None and "Manage roles" in reply


async def test_a_readable_bot_keeps_the_plain_refusal(monkeypatch):
    bot, _client = make(monkeypatch)
    bot.permissions_readable = None
    assert bot._permission_refusal(MissingPermissions("manage_server")) is None


async def test_the_assigned_prefix_wins_over_the_configured_one(monkeypatch, caplog):
    bot, _client = make(monkeypatch)
    assert bot.prefix == "!"
    with caplog.at_level(logging.WARNING):
        await bot._adopt_assigned_prefix({"id": "bot-1", "bot_prefix": "~"})
    assert bot.prefix == "~"
    assert any("'~'" in r.message for r in caplog.records)


async def test_no_assigned_prefix_leaves_the_configured_one(monkeypatch):
    bot, _client = make(monkeypatch)
    await bot._adopt_assigned_prefix({"id": "bot-1"})
    assert bot.prefix == "!"


async def test_a_command_clash_is_logged_loudly_and_the_bot_keeps_running(monkeypatch, caplog):
    bot, client = make(monkeypatch)
    client.respond("PUT", "/bots/commands", ApiError(409, {"error": "!watch is already registered by jellyfin"}))
    with caplog.at_level(logging.ERROR):
        await bot._register_commands()
    assert any("already registered by jellyfin" in r.message for r in caplog.records)


async def test_register_commands_raises_a_conflict_on_409(monkeypatch):
    _bot, client = make(monkeypatch)
    client.respond("PUT", "/bots/commands", ApiError(409, {"error": "taken"}))
    try:
        await register_commands(client, prefix="!", commands=[])
    except RegistrationConflict as err:
        assert err.reason == "taken"
    else:
        raise AssertionError("a 409 must raise RegistrationConflict")
