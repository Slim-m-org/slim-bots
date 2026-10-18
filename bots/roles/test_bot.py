#!/usr/bin/env python3
"""Command-layer tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import tempfile

os.environ.setdefault("SLIMM_ROLES", "member:r-member,helper:r-helper")

import bot as roles
from slimbots.http import ApiError
from slimbots.permissions import Permissions
from slimbots.testing import FakeAsyncClient, human, run_module_tests, wire_bot

MEMBERS = [human("u1", "nick", "Nick", role_ids=[])]
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
    wire_bot(roles.bot, client, me_id="bot-1")
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


def setup_menu(*, manager=True):
    """A fresh store per test, and u1 holding Manage Roles unless `manager` is False."""
    client = setup()
    roles.bot.store = None
    roles.bot.data_path = os.path.join(tempfile.mkdtemp(), "roles.db")
    asyncio.run(roles.bot.open_store(migrate=roles.menu_store.init_db))
    member = roles.bot.space.members["u1"]
    member.role_ids = []
    member._base_permissions = Permissions.MANAGE_ROLES if manager else 0
    return client


def make_menu(client, args="Pick your roles | member, helper", msg_id="m1"):
    process(client, message("u1", f"!rolemenu create {args}", msg_id))
    return client.sent[-1]["id"] if client.sent else None


def press(client, menu_id, role_id, interaction_id="i1"):
    frame = {
        "type": "interaction.created", "interaction_id": interaction_id, "channel_id": "c1", "message_id": menu_id,
        "custom_id": f"rolemenu:{role_id}", "user_id": "u1", "user_display_name": "Nick", "created_at": 1,
    }

    async def run():
        await roles.bot._handle_frame(frame)
        await asyncio.gather(*list(roles.bot._background_tasks))

    asyncio.run(run())


def button_ids(sent):
    return [b["custom_id"] for row in sent["components"] for b in row["buttons"]]


def test_a_manager_creates_a_menu_with_one_button_per_role():
    client = setup_menu()
    menu_id = make_menu(client)
    sent = client.sent[-1]
    assert sent["content"] == "Pick your roles"
    assert button_ids(sent) == ["rolemenu:r-member", "rolemenu:r-helper"]
    assert menu_id in client.ephemerals[-1]["content"]


def test_a_role_off_the_self_assignable_list_cannot_go_in_a_menu():
    client = setup_menu()
    make_menu(client, "Pick | member, admin")
    assert "`admin` is not on the self-assignable list" in client.ephemerals[-1]["content"]
    assert not client.sent


def test_a_non_manager_cannot_create_a_menu():
    client = setup_menu(manager=False)
    make_menu(client)
    assert "Manage Roles" in client.ephemerals[-1]["content"]
    assert not client.sent


def test_title_and_role_count_are_limited():
    client = setup_menu()
    make_menu(client, "x" * 201 + " | member")
    assert "under 200" in client.ephemerals[-1]["content"]
    many = {f"r{i}": f"id-{i}" for i in range(26)}
    with_roles(many)
    make_menu(client, "Big | " + ", ".join(many))
    assert "at most 25" in client.ephemerals[-1]["content"]
    assert not client.sent


def test_a_press_grants_then_a_second_press_removes():
    client = setup_menu()
    menu_id = make_menu(client)
    press(client, menu_id, "r-member")
    assert ("PUT", "/members/u1/roles/r-member") in [(m, p) for m, p, *_ in client.calls]
    assert "added `member`" in client.ephemerals[-1]["content"]
    press(client, menu_id, "r-member", "i2")
    assert ("DELETE", "/members/u1/roles/r-member") in [(m, p) for m, p, *_ in client.calls]
    assert "removed `member`" in client.ephemerals[-1]["content"]


def test_pick_one_swaps_the_other_menu_role():
    client = setup_menu()
    menu_id = make_menu(client, "--one Pick one | member, helper")
    press(client, menu_id, "r-member")
    press(client, menu_id, "r-helper", "i2")
    calls = [(m, p) for m, p, *_ in client.calls]
    assert ("PUT", "/members/u1/roles/r-helper") in calls
    assert ("DELETE", "/members/u1/roles/r-member") in calls
    assert "added `helper`" in client.ephemerals[-1]["content"]
    assert "removed `member`" in client.ephemerals[-1]["content"]


def test_a_menu_without_pick_one_keeps_the_other_role():
    client = setup_menu()
    menu_id = make_menu(client)
    press(client, menu_id, "r-member")
    press(client, menu_id, "r-helper", "i2")
    assert ("DELETE", "/members/u1/roles/r-member") not in [(m, p) for m, p, *_ in client.calls]


def test_a_press_for_a_role_no_longer_self_assignable_is_refused():
    client = setup_menu()
    menu_id = make_menu(client)
    with_roles({"member": "r-member"})
    press(client, menu_id, "r-helper")
    assert "no longer offered" in client.ephemerals[-1]["content"]
    assert not [c for c in client.calls if c[0] == "PUT" and "/roles/" in c[1]]


def test_a_forged_custom_id_cannot_grant_a_role_outside_the_menu():
    client = setup_menu()
    menu_id = make_menu(client, "Only member | member")
    with_roles({"member": "r-member", "helper": "r-helper"})
    press(client, menu_id, "r-helper")
    assert "not part of this menu" in client.ephemerals[-1]["content"]
    assert not [c for c in client.calls if c[0] == "PUT" and "/roles/" in c[1]]
    press(client, "unknown-message", "r-member", "i2")
    assert "no longer exists" in client.ephemerals[-1]["content"]


def test_a_role_deleted_from_the_space_answers_privately():
    client = setup_menu()
    menu_id = make_menu(client)
    client.respond("PUT", "/members/u1/roles/r-member", ApiError(404))
    press(client, menu_id, "r-member")
    assert "no longer exists in this space" in client.ephemerals[-1]["content"]


def test_a_role_the_bot_cannot_manage_explains_why():
    client = setup_menu()
    menu_id = make_menu(client)
    client.respond("PUT", "/members/u1/roles/r-helper", ApiError(403))
    press(client, menu_id, "r-helper")
    assert "MANAGE_MESSAGES" in client.ephemerals[-1]["content"]


def test_menus_survive_a_restart_of_the_store():
    client = setup_menu()
    menu_id = make_menu(client)
    path = roles.bot.data_path
    roles.bot.store = None
    asyncio.run(roles.bot.open_store(migrate=roles.menu_store.init_db))
    assert roles.bot.data_path == path
    press(client, menu_id, "r-member")
    assert "added `member`" in client.ephemerals[-1]["content"]


def test_adding_and_removing_a_role_edits_the_buttons_in_place():
    client = setup_menu()
    menu_id = make_menu(client, "Pick | member")
    process(client, message("u1", f"!rolemenu add {menu_id} helper", "m2"))
    assert [b["custom_id"] for r in client.component_edits[-1]["components"] for b in r["buttons"]] == ["rolemenu:r-member", "rolemenu:r-helper"]
    process(client, message("u1", f"!rolemenu remove {menu_id} member", "m3"))
    assert [b["custom_id"] for r in client.component_edits[-1]["components"] for b in r["buttons"]] == ["rolemenu:r-helper"]
    process(client, message("u1", f"!rolemenu remove {menu_id} helper", "m4"))
    assert "at least one role" in client.ephemerals[-1]["content"]
    process(client, message("u1", f"!rolemenu add {menu_id} admin", "m5"))
    assert "not on the self-assignable list" in client.ephemerals[-1]["content"]


def test_deleting_a_menu_removes_the_message_and_stops_presses():
    client = setup_menu()
    menu_id = make_menu(client)
    process(client, message("u1", f"!rolemenu delete {menu_id}", "m2"))
    assert ("DELETE", f"/channels/c1/messages/{menu_id}") in [(m, p) for m, p, *_ in client.calls]
    press(client, menu_id, "r-member")
    assert "no longer exists" in client.ephemerals[-1]["content"]


def test_only_a_manager_can_edit_or_delete_a_menu():
    client = setup_menu()
    menu_id = make_menu(client)
    roles.bot.space.members["u1"]._base_permissions = 0
    process(client, message("u1", f"!rolemenu delete {menu_id}", "m2"))
    assert "Manage Roles" in client.ephemerals[-1]["content"]
    press(client, menu_id, "r-member")
    assert "added `member`" in client.ephemerals[-1]["content"]


if __name__ == "__main__":
    run_module_tests(globals())
