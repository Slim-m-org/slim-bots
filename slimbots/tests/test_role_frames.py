from slimbots import Bot, Permissions
from slimbots.authors import AuthorFilter
from slimbots.http import ApiError
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

ROLES = [
    {"id": "everyone", "name": "@everyone", "permissions": 0, "is_everyone": True},
    {"id": "approver", "name": "Approver", "permissions": Permissions.MANAGE_SERVER, "is_everyone": False},
]


def member(role_ids, user_id="u1"):
    return {"id": user_id, "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": role_ids}


async def make(monkeypatch, initial_roles):
    monkeypatch.delenv("SLIMM_PREFIX", raising=False)
    client = FakeAsyncClient()
    bot = Bot(channels={"text"})
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space, ignore_bots=True)
    client.respond("GET", "/roles", ROLES)
    client.respond("GET", "/members", [member(initial_roles)])
    await bot.space.refresh_roles()
    await bot.space.refresh_members()
    return bot, client


async def test_a_revoked_role_stops_granting_permission_after_member_role_changed(monkeypatch):
    bot, client = await make(monkeypatch, ["approver"])
    assert (await bot.space.resolve_member("u1")).has_permission(Permissions.MANAGE_SERVER)
    client.respond("GET", "/users/u1", member([]))
    await bot._handle_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "approver"})
    assert not (await bot.space.resolve_member("u1")).has_permission(Permissions.MANAGE_SERVER), "revoked role still grants MANAGE_SERVER"


async def test_a_newly_granted_role_gives_permission_after_member_role_changed(monkeypatch):
    bot, client = await make(monkeypatch, [])
    client.respond("GET", "/users/u1", member(["approver"]))
    await bot._handle_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "approver"})
    assert (await bot.space.resolve_member("u1")).has_permission(Permissions.MANAGE_SERVER), "newly granted role not seen"


async def test_a_role_permission_edit_is_applied_to_every_cached_member_after_role_changed(monkeypatch):
    bot, client = await make(monkeypatch, ["approver"])
    client.respond("GET", "/roles", [ROLES[0], {**ROLES[1], "permissions": 0}])
    await bot._handle_frame({"type": "role.changed", "role_id": "approver"})
    assert not (await bot.space.resolve_member("u1")).has_permission(Permissions.MANAGE_SERVER), "role permission edit not applied"


async def test_a_role_change_the_bot_may_not_read_is_ignored_not_raised(monkeypatch):
    bot, client = await make(monkeypatch, ["approver"])
    client.respond("GET", "/roles", ApiError(403, {"error": "forbidden"}))
    await bot._handle_frame({"type": "role.changed", "role_id": "approver"})
    assert (await bot.space.resolve_member("u1")).has_permission(Permissions.MANAGE_SERVER)


async def test_a_role_change_for_a_member_not_in_the_cache_costs_no_request(monkeypatch):
    bot, client = await make(monkeypatch, [])
    before = len(client.calls)
    await bot._handle_frame({"type": "member.role_changed", "user_id": "stranger", "role_id": "approver"})
    assert len(client.calls) == before
