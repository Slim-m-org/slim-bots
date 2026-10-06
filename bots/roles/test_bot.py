#!/usr/bin/env python3
"""Command-layer tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SLIMM_ROLES", "member:r-member,helper:r-helper")

import bot as roles  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

MEMBERS = [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}]
ROLE_DEFS = [
    {"id": "r-everyone", "name": "@everyone", "permissions": 0, "is_everyone": True},
    {"id": "r-member", "name": "member", "permissions": 0, "is_everyone": False},
    {"id": "r-helper", "name": "helper", "permissions": 8, "is_everyone": False},  # MANAGE_MESSAGES
]


def message(author_id, content, msg_id="m1"):
    return {"id": msg_id, "author_id": author_id, "channel_id": "c1", "content": content}


def setup(*, my_permissions=32):  # MANAGE_ROLES
    roles.ROLES.clear()
    roles.ROLES.update({"member": "r-member", "helper": "r-helper"})
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    client.respond("GET", "/roles", ROLE_DEFS)
    client.respond("GET", "/me", {"id": "bot-1", "permissions": my_permissions})
    roles.bot.channels = {"c1"}
    client.respond("PATCH", f"/channels/c1/messages/{roles.listing_message_id()}", None)
    roles.bot.client = client
    roles.bot.space = Space(client)
    roles.bot.authors = AuthorFilter(client, space=roles.bot.space, ignore_bots=True)
    roles.bot.me_id = "bot-1"
    asyncio.run(roles.bot.space.refresh_members())
    return client


def process(client, *messages):
    async def run():
        for msg in messages:
            await roles.bot.process_message(msg)

    asyncio.run(run())


def test_roles_lists_the_offer():
    client = setup()
    process(client, message("u1", "!roles"))
    assert "member" in client.sent[-1]["content"]
    assert "helper" in client.sent[-1]["content"]


def test_role_grants_a_role_the_bot_can_hand_out():
    client = setup()
    client.respond("PUT", "/members/u1/roles/r-member", None)
    process(client, message("u1", "!role member"))
    assert "you have `member` now" in client.sent[-1]["content"]


class prefixed:
    """Runs a block with the bot answering to `?`, the way `SLIMM_PREFIX` would set it."""

    def __enter__(self):
        self.original, roles.bot.prefix = roles.bot.prefix, "?"

    def __exit__(self, *_exc):
        roles.bot.prefix = self.original


def test_role_replies_name_the_configured_prefix():
    client = setup()
    with prefixed():
        process(client, message("u1", "?role admin"))
        assert "try `?roles`" in client.sent[-1]["content"]
        assert "`?role <name>`" in roles.listing_text()


def test_the_roles_setting_parses_into_an_ordered_name_to_id_map():
    assert roles.ROLES == {"member": "r-member", "helper": "r-helper"}
    assert list(roles.ROLES) == ["member", "helper"]


def with_roles(mapping):
    roles.ROLES.clear()
    roles.ROLES.update(mapping)


def test_a_multi_word_role_name_can_be_requested_and_removed():
    client = setup()
    with_roles({"Game Night": "r-member"})
    client.respond("PUT", "/members/u1/roles/r-member", None)
    client.respond("DELETE", "/members/u1/roles/r-member", None)
    process(client, message("u1", "!role Game Night"))
    assert "you have `Game Night` now" in client.sent[-1]["content"], client.sent[-1]["content"]
    process(client, message("u1", "!role remove game  night", "m2"))
    assert "removed `Game Night`" in client.sent[-1]["content"], client.sent[-1]["content"]


def test_role_lookup_ignores_case():
    client = setup()
    with_roles({"gamer": "r-member"})
    client.respond("PUT", "/members/u1/roles/r-member", None)
    process(client, message("u1", "!role GAMER"))
    assert "you have `gamer` now" in client.sent[-1]["content"], client.sent[-1]["content"]


def test_names_the_subcommands_or_one_another_are_refused_at_startup():
    assert roles.unusable_role_names({"member": "a", "Mine": "b", "remove": "c", "Remove x": "d"}) == ["Mine", "remove", "Remove x"]
    assert roles.unusable_role_names({"Gamer": "a", "gamer": "b"}) == ["gamer"]
    assert roles.unusable_role_names({"member": "a", "Game Night": "b"}) == []


def test_role_refuses_an_unlisted_name():
    client = setup()
    process(client, message("u1", "!role admin"))
    assert "no role called" in client.sent[-1]["content"]


def test_role_remove_revokes():
    client = setup()
    client.respond("DELETE", "/members/u1/roles/r-member", None)
    process(client, message("u1", "!role remove member"))
    assert "removed `member`" in client.sent[-1]["content"]


def test_role_mine_lists_held_roles():
    client = setup()
    roles.bot.space.members["u1"].role_ids = ["r-member"]
    process(client, message("u1", "!role mine"))
    assert "member" in client.sent[-1]["content"]


def test_role_mine_says_none_when_empty():
    client = setup()
    process(client, message("u1", "!role mine"))
    assert "none of the roles" in client.sent[-1]["content"]


def test_escalation_names_the_missing_permission_when_bot_lacks_manage_roles():
    from slimbots.http import ApiError

    client = setup(my_permissions=0)
    client.respond("PUT", "/members/u1/roles/r-helper", ApiError(403, {"error": "forbidden"}))
    process(client, message("u1", "!role helper"))
    assert "MANAGE_ROLES" in client.sent[-1]["content"]


def test_escalation_names_the_missing_permission_when_role_carries_more():
    from slimbots.http import ApiError

    client = setup(my_permissions=32)  # MANAGE_ROLES only, not MANAGE_MESSAGES
    client.respond("PUT", "/members/u1/roles/r-helper", ApiError(403, {"error": "forbidden"}))
    process(client, message("u1", "!role helper"))
    assert "MANAGE_MESSAGES" in client.sent[-1]["content"]


def test_roles_status_reports_grantable_and_missing():
    client = setup(my_permissions=32)
    process(client, message("u1", "!roles status"))
    reply = client.sent[-1]["content"]
    assert "`member`: grantable" in reply
    assert "`helper`: missing MANAGE_MESSAGES" in reply


def test_a_refused_revoke_names_the_missing_manage_roles():
    from slimbots.http import ApiError

    client = setup(my_permissions=0)
    client.respond("DELETE", "/members/u1/roles/r-member", ApiError(403, {"error": "forbidden"}))
    process(client, message("u1", "!role remove member"))
    assert "MANAGE_ROLES" in client.sent[-1]["content"]


def test_a_missing_role_on_grant_is_called_misconfigured():
    from slimbots.http import ApiError

    client = setup()
    client.respond("PUT", "/members/u1/roles/r-member", ApiError(404, {"error": "not found"}))
    process(client, message("u1", "!role member"))
    assert "misconfigured" in client.sent[-1]["content"]


def test_status_sees_a_permission_granted_after_connect():
    client = setup(my_permissions=0)
    process(client, message("u1", "!roles status"))
    assert "MANAGE_ROLES is missing" in client.sent[-1]["content"]
    client.respond("GET", "/me", {"id": "bot-1", "permissions": 32})
    process(client, message("u1", "!roles status", "m2"))
    assert "MANAGE_ROLES is missing" not in client.sent[-1]["content"], client.sent[-1]["content"]


def test_status_sees_an_edited_role():
    client = setup(my_permissions=32)
    process(client, message("u1", "!roles status"))
    assert "`helper`: missing MANAGE_MESSAGES" in client.sent[-1]["content"]
    client.respond("GET", "/roles", [dict(r, permissions=0) if r["id"] == "r-helper" else r for r in ROLE_DEFS])
    process(client, message("u1", "!roles status", "m2"))
    assert "`helper`: grantable" in client.sent[-1]["content"], client.sent[-1]["content"]


def test_a_refused_grant_explains_with_the_permissions_held_now():
    from slimbots.http import ApiError

    client = setup(my_permissions=0)
    client.respond("PUT", "/members/u1/roles/r-helper", ApiError(403, {"error": "forbidden"}))
    process(client, message("u1", "!role helper"))
    assert "MANAGE_ROLES" in client.sent[-1]["content"]
    client.respond("GET", "/me", {"id": "bot-1", "permissions": 32})
    process(client, message("u1", "!role helper", "m2"))
    assert "also carries MANAGE_MESSAGES" in client.sent[-1]["content"], client.sent[-1]["content"]


def test_the_listing_is_posted_when_the_old_one_is_gone_and_other_errors_propagate():
    from slimbots.http import ApiError

    client = setup()
    client.respond("PATCH", f"/channels/c1/messages/{roles.listing_message_id()}", ApiError(404, {"error": "not found"}))
    client.respond("POST", "/channels/c1/messages", {"id": roles.listing_message_id()})
    asyncio.run(roles.post_listing())
    assert "Self-service roles" in client.sent[-1]["content"]
    client.respond("PATCH", f"/channels/c1/messages/{roles.listing_message_id()}", ApiError(500, {"error": "boom"}))
    try:
        asyncio.run(roles.post_listing())
    except ApiError as err:
        assert err.status == 500
    else:
        raise AssertionError("a non-404 edit failure must propagate")


def test_another_bot_is_ignored_by_default():
    client = setup()
    client.respond(
        "GET",
        "/members",
        MEMBERS + [{"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": []}],
    )
    asyncio.run(roles.bot.space.refresh_members())
    process(client, message("bot-2", "!role member"))
    assert client.sent == []


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
