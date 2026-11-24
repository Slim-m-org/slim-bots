"""The cached member and role state follows timeouts, ownership, lost role read and the connect window."""

import asyncio
import sqlite3

import slimbots.bot as botmod
from slimbots import Bot, Permissions, cursor
from slimbots.authors import AuthorFilter
from slimbots.http import ApiError
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

ROLES = [
    {"id": "everyone", "name": "@everyone", "permissions": Permissions.SEND_MESSAGES, "is_everyone": True},
    {"id": "approver", "name": "Approver", "permissions": Permissions.MANAGE_SERVER, "is_everyone": False},
    {"id": "reader", "name": "Reader", "permissions": Permissions.MANAGE_ROLES, "is_everyone": False},
]


def person(user_id="u1", role_ids=(), **extra):
    return {"id": user_id, "username": user_id, "display_name": user_id.title(), "is_bot": False, "is_webhook": False,
            "role_ids": list(role_ids), **extra}


async def a_bot(*members):
    client = FakeAsyncClient(me_id="bot-1")
    bot = Bot(channels={"c1"})
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space)
    client.respond("GET", "/roles", ROLES)
    client.respond("GET", "/members", list(members) or [person()])
    await bot.space.refresh_roles()
    await bot.space.refresh_members()
    return bot, client


async def test_a_timeout_frame_updates_the_cached_member_and_a_lifted_one_clears_it():
    bot, _client = await a_bot()
    await bot._route_frame({"type": "member.timeout", "user_id": "u1", "until": 4_000_000_000_000})
    assert bot.space.members["u1"].timed_out_until == 4_000_000_000_000
    await bot._route_frame({"type": "member.timeout", "user_id": "u1", "until": None})
    assert bot.space.members["u1"].timed_out_until is None


async def test_a_timeout_frame_for_a_member_not_in_the_cache_costs_no_request():
    bot, client = await a_bot()
    before = len(client.calls)
    await bot._route_frame({"type": "member.timeout", "user_id": "stranger", "until": 4_000_000_000_000})
    assert len(client.calls) == before and "stranger" not in bot.space.members


async def test_a_member_accepting_the_rules_is_refetched():
    bot, client = await a_bot()
    client.respond("GET", "/users/u1", person(role_ids=["approver"]))
    await bot._route_frame({"type": "join_gate.changed", "user_id": "u1"})
    assert bot.space.members["u1"].has_permission(Permissions.MANAGE_SERVER)


async def test_the_owner_holds_every_permission_whatever_their_roles():
    bot, _client = await a_bot(person("owner", is_owner=True), person("u1"))
    owner = bot.space.members["owner"]
    assert owner.is_owner and not bot.space.members["u1"].is_owner
    for name, bit in vars(Permissions).items():
        if name.isupper() and name != "NONE":
            assert owner.has_permission(bit), name
    bot.space.reapply_roles()
    assert owner.has_permission(Permissions.MANAGE_MESSAGES), "a role reload dropped the owner's bits"


async def test_the_owner_keeps_every_permission_when_the_role_table_is_unreadable():
    client = FakeAsyncClient(me_id="bot-1")
    space = Space(client)
    client.respond("GET", "/users/owner", person("owner", is_owner=True))
    assert (await space.fetch_member("owner")).has_permission(Permissions.MANAGE_MESSAGES)


async def test_an_ownership_hand_off_refetches_the_old_and_the_new_owner():
    bot, client = await a_bot(person("old", is_owner=True), person("heir"))
    client.respond("GET", "/users/old", person("old"))
    client.respond("GET", "/users/heir", person("heir", is_owner=True))
    await bot._route_frame({"type": "owner.changed", "user_id": "heir"})
    assert not bot.space.members["old"].has_permission(Permissions.MANAGE_SERVER)
    assert bot.space.members["heir"].has_permission(Permissions.MANAGE_SERVER)


async def test_losing_role_read_drops_the_old_table_so_no_member_keeps_or_gets_a_stale_grant():
    bot, client = await a_bot(person(role_ids=["approver"]))
    client.respond("GET", "/roles", ApiError(403, {"error": "forbidden"}))
    client.respond("GET", f"/members/{bot.me_id}/permissions", {"user_id": bot.me_id, "permissions": 0})
    await bot._route_frame({"type": "role.changed", "role_id": "approver"})
    assert bot.space.roles == {} and bot.space.roles_readable is False
    assert not bot.space.members["u1"].has_permission(Permissions.MANAGE_SERVER), "a cached member kept a stale grant"
    client.respond("GET", "/users/u2", person("u2", role_ids=["approver"]))
    assert not (await bot.space.fetch_member("u2")).has_permission(Permissions.MANAGE_SERVER), "a new member got a stale grant"


async def test_a_reconnect_after_losing_role_read_derives_no_bits_from_the_old_table():
    bot, client = await a_bot(person(role_ids=["approver"]))
    client.respond("GET", "/roles", ApiError(403, {"error": "forbidden"}))
    client.respond("GET", "/members/bot-1/permissions", {"user_id": "bot-1", "permissions": 0})
    assert await bot._load_roles() is False
    await bot.space.refresh_members()
    assert not bot.space.members["u1"].has_permission(Permissions.MANAGE_SERVER)
    assert bot.space.members["u1"].has_permission(Permissions.SEND_MESSAGES) is False


async def test_a_role_change_on_the_bot_itself_re_probes_role_read():
    client = FakeAsyncClient(me_id="bot-1")
    bot = Bot(channels={"c1"})
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space)
    client.respond("GET", "/roles", ApiError(403, {"error": "forbidden"}))
    client.respond("GET", "/members/bot-1/permissions", {"user_id": "bot-1", "permissions": 0})
    client.respond("GET", "/members", [person(role_ids=["approver"])])
    await bot._load_roles()
    await bot.space.refresh_members()
    assert bot.space.roles_readable is False and bot.permissions_readable is True
    client.respond("GET", "/roles", ROLES)
    await bot._route_frame({"type": "member.role_changed", "user_id": "bot-1", "role_id": "reader"})
    assert bot.space.roles_readable is True and "approver" in bot.space.roles
    assert bot.space.members["u1"].has_permission(Permissions.MANAGE_SERVER)
    assert bot.permissions_readable is None


async def test_losing_role_read_on_the_bot_itself_is_noticed_without_waiting_for_a_role_edit():
    bot, client = await a_bot(person(role_ids=["approver"]))
    client.respond("GET", "/roles", ApiError(403, {"error": "forbidden"}))
    client.respond("GET", "/members/bot-1/permissions", {"user_id": "bot-1", "permissions": 0})
    await bot._route_frame({"type": "member.role_changed", "user_id": "bot-1", "role_id": "reader"})
    assert bot.space.roles_readable is False and bot.permissions_readable is True
    assert not bot.space.members["u1"].has_permission(Permissions.MANAGE_SERVER)


class _Gateway:
    def __init__(self, sockets):
        self.hello = {}
        self.queue = asyncio.Queue()
        sockets.append(self.queue)

    async def frames(self):
        while True:
            frame = await self.queue.get()
            if frame is None:
                return
            yield frame

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None


async def test_a_role_revoked_while_the_bot_connects_is_applied(monkeypatch):
    monkeypatch.delenv("SLIMM_PREFIX", raising=False)
    client = FakeAsyncClient(me_id="bot-1")
    bot = Bot(channels={"c1"})
    bot.client, bot.space = client, Space(client)
    bot.authors = AuthorFilter(client, space=bot.space)
    bot._cursor_conn = sqlite3.connect(":memory:", isolation_level=None)
    cursor.init_table(bot._cursor_conn)
    cursor.set(bot._cursor_conn, "c1", 0)
    client.respond("POST", "/sync", {"scopes": []})
    client.respond("GET", "/roles", ROLES)
    state = {"roles": ["approver"]}
    client.respond("GET", "/members", lambda *_a, **_k: [person(role_ids=state["roles"])])
    client.respond("GET", "/users/u1", lambda *_a, **_k: person(role_ids=state["roles"]))
    sockets = []

    async def open_(_client, **_kwargs):
        return _Gateway(sockets)

    monkeypatch.setattr(botmod.Gateway, "open", open_)

    @bot.event
    async def on_connect():
        state["roles"] = []
        for queue in sockets:
            queue.put_nowait({"type": "member.role_changed", "user_id": "u1", "role_id": "approver"})

    @bot.event
    async def on_ready():
        for queue in sockets:
            queue.put_nowait(None)

    await asyncio.wait_for(bot._connect_once(lambda: None), timeout=3)
    assert not bot.space.members["u1"].has_permission(Permissions.MANAGE_SERVER), "a revoke during connect was lost"
