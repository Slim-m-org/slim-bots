"""A Member argument resolves by id, then username, and takes a display name only when exactly one member has it."""

import pytest

from slimbots.commands import BadArgument, _convert_member
from slimbots.http import ApiError
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient, human

# Roster order is id order, so the older impostor comes first, as it would from `GET /members`.
ROSTER = [
    human("u1", "mallory", "alice"),
    human("u2", "alice", "Alice Liddell"),
    human("u3", "sam1", "Sam"),
    human("u4", "sam2", "sam"),
    human("u5", "bob", "Bobby"),
]


async def a_space():
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", ROSTER)
    space = Space(client)
    await space.refresh_members()
    return space, client


def a_ctx(space):
    return type("Ctx", (), {"bot": type("B", (), {"space": space})()})()


async def test_a_username_beats_an_older_members_matching_display_name():
    space, _client = await a_space()
    assert (await space.get_member("alice")).id == "u2"
    assert (await space.get_member("ALICE")).id == "u2"


async def test_an_exact_id_still_resolves_first():
    space, _client = await a_space()
    assert (await space.get_member("u1")).id == "u1"


async def test_a_display_name_only_one_member_has_still_resolves():
    space, _client = await a_space()
    assert (await space.get_member("bobby")).id == "u5"


async def test_a_display_name_two_members_share_resolves_to_nobody():
    space, _client = await a_space()
    assert await space.get_member("sam") is None


async def test_the_converter_asks_for_the_username_when_a_display_name_is_shared():
    space, _client = await a_space()
    with pytest.raises(BadArgument, match="@username"):
        await _convert_member(a_ctx(space), "sam")


async def test_an_at_mention_matches_usernames_only():
    space, client = await a_space()
    assert (await _convert_member(a_ctx(space), "@alice")).id == "u2"
    client.respond("GET", "/users/bobby", ApiError(404, {"error": "not_found"}))
    with pytest.raises(BadArgument, match="no member called"):
        await _convert_member(a_ctx(space), "@bobby")


async def test_resolve_member_reads_a_stored_id_and_never_a_name():
    space, client = await a_space()
    client.respond("GET", "/users/alice", ApiError(404, {"error": "not_found"}))
    assert (await space.resolve_member("u2")).id == "u2"
    with pytest.raises(ApiError):
        await space.resolve_member("alice")
