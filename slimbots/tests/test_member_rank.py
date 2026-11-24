"""A member's place on the role ladder: read from the member payload, else worked out from the role table."""

from slimbots.http import ApiError
from slimbots.models import Member, Role
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

USER = {"id": "u1", "username": "nick", "display_name": "Nick", "role_ids": []}
PEER = {"id": "u2", "username": "ann", "display_name": "Ann", "role_ids": []}


def ranked(user, position, *, owner=False):
    return Member({**user, "top_role_position": position, "is_owner": owner})


def test_a_role_carries_its_position_and_none_when_the_server_sends_none():
    assert Role({"id": "r1", "name": "Mod", "position": 4}).position == 4
    assert Role({"id": "r1", "name": "Mod"}).position is None


def test_only_a_strictly_higher_top_role_outranks():
    assert ranked(USER, 5).outranks(ranked(PEER, 4)) is True
    assert ranked(USER, 5).outranks(ranked(PEER, 5)) is False
    assert ranked(USER, 4).outranks(ranked(PEER, 5)) is False


def test_holding_no_role_ranks_below_every_role():
    assert ranked(USER, 0).outranks(ranked(PEER, None)) is True
    assert ranked(USER, None).outranks(ranked(PEER, 0)) is False
    assert ranked(USER, None).outranks(ranked(PEER, None)) is False


def test_the_owner_outranks_everyone_and_nobody_outranks_the_owner():
    assert ranked(USER, None, owner=True).outranks(ranked(PEER, 9)) is True
    assert ranked(USER, 9).outranks(ranked(PEER, None, owner=True)) is False


def test_an_unknown_rank_outranks_nobody_for_certain():
    assert Member(USER).rank_known is False
    assert Member(USER).outranks(ranked(PEER, None)) is None
    assert ranked(USER, 5).outranks(Member(PEER)) is None


async def test_without_the_payload_field_the_rank_comes_from_the_role_table():
    client = FakeAsyncClient()
    client.respond("GET", "/roles", [
        {"id": "r-every", "name": "@everyone", "position": 0, "is_everyone": True},
        {"id": "r-mod", "name": "Mod", "position": 3},
        {"id": "r-vip", "name": "VIP", "position": 6},
    ])
    space = Space(client)
    await space.refresh_roles()
    member = space._make_member({**USER, "role_ids": ["r-every", "r-mod"]})
    assert member.rank_known and member.top_role_position == 3
    await space.grant_role(member, "r-vip")
    assert member.top_role_position == 6
    await space.revoke_role(member, "r-vip")
    await space.revoke_role(member, "r-mod")
    assert member.rank_known and member.top_role_position is None


async def test_the_payload_field_wins_over_a_stale_role_table():
    client = FakeAsyncClient()
    client.respond("GET", "/roles", [{"id": "r-mod", "name": "Mod", "position": 3}])
    space = Space(client)
    await space.refresh_roles()
    member = space._make_member({**USER, "role_ids": ["r-mod"], "top_role_position": 7})
    assert member.top_role_position == 7


async def test_a_role_table_without_positions_leaves_the_rank_unknown():
    client = FakeAsyncClient()
    client.respond("GET", "/roles", [{"id": "r-mod", "name": "Mod"}])
    space = Space(client)
    await space.refresh_roles()
    assert space._make_member({**USER, "role_ids": ["r-mod"]}).rank_known is False


async def test_refresh_rank_rereads_the_ladder_without_touching_permissions():
    client = FakeAsyncClient()
    member = Member(USER, base_permissions=8)
    client.respond("GET", "/users/u1", {**USER, "top_role_position": 2, "is_owner": True})
    assert await Space(client).refresh_rank(member) is True
    assert member.top_role_position == 2 and member.is_owner and member.has_permission(8)


async def test_ladder_reach_rereads_both_ranks_and_is_none_when_either_is_unreadable():
    client = FakeAsyncClient()
    space = Space(client)
    invoker, target = Member(USER), Member(PEER)
    client.respond("GET", "/users/u1", {**USER, "top_role_position": 5})
    client.respond("GET", "/users/u2", {**PEER, "top_role_position": 5})
    assert await space.ladder_reach(invoker, target) is False
    client.respond("GET", "/users/u2", {**PEER, "top_role_position": 4})
    assert await space.ladder_reach(invoker, target) is True
    client.respond("GET", "/users/u2", PEER)
    assert await space.ladder_reach(invoker, target) is None
    client.respond("GET", "/users/u2", ApiError(500, {"error": "boom"}))
    assert await space.ladder_reach(invoker, target) is None
