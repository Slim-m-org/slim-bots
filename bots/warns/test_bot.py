#!/usr/bin/env python3
"""Commands and escalation against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as warns  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.testing import FakeAsyncClient, handle_frame, human, run_module_tests, wire_bot  # noqa: E402

MOD = human("u-mod", "mod", "Mod")
ANN = human("u-ann", "ann", "Ann")
_counter = [0]


def setup(**config):
    for name, value in {"LOG_CHANNEL": "", "EXPIRE_DAYS": 0, **config}.items():
        setattr(warns, name, value)
    client = FakeAsyncClient(me_id="bot-1")
    rank(client, MOD, 5)
    rank(client, ANN, None)
    wire_bot(warns.bot, client, db_name="warns.db")
    asyncio.run(warns.bot.space.refresh_members())
    asyncio.run(warns.bot.open_store(migrate=warns.init_db))
    client.respond("PUT", "/members/u-ann/removal", None)
    return client


def rank(client, user, position, *, owner=False):
    """Serves `user` at `position` on the role ladder; None holds no role."""
    client.respond("GET", f"/users/{user['id']}", {**user, "role_ids": [], "top_role_position": position, "is_owner": owner})


FULL_MOD = Permissions.KICK_MEMBERS | Permissions.BAN_MEMBERS | Permissions.MANAGE_SERVER


def say(text, user=MOD, permissions=FULL_MOD):
    _counter[0] += 1
    member = warns.bot.space._make_member({**user, "role_ids": []})
    member._base_permissions = permissions
    warns.bot.space.members[user["id"]] = member
    frame = {"id": f"m{_counter[0]}", "channel_id": "c1", "author_id": user["id"], "content": text, "seq": _counter[0]}
    asyncio.run(warns.bot.process_message(frame))


def seed_member(client, permissions=0):
    member = warns.bot.space._make_member({**ANN, "role_ids": []})
    member._base_permissions = permissions
    warns.bot.space.members["u-ann"] = member
    return member


def calls(client, method, path):
    return [c for c in client.calls if c[0] == method and c[1].startswith(path)]


def test_a_warn_is_counted_and_listed():
    client = setup()
    seed_member(client)
    say("!warn @ann spamming links")
    say("!warn @ann again")
    assert "2 active warns" in client.sent[-1]["content"]
    say("!warns @ann")
    text = client.ephemerals[-1]["content"]
    assert "spamming links" in text and "again" in text


def test_nothing_happens_before_a_rule_and_the_rule_fires_at_its_count():
    client = setup()
    seed_member(client)
    say("!warnrule add 2 timeout 1h")
    say("!warn @ann one")
    assert not calls(client, "PUT", "/members/u-ann/timeout")
    say("!warn @ann two")
    sent = calls(client, "PUT", "/members/u-ann/timeout")
    assert sent and sent[0][2]["duration_seconds"] == 3600
    assert "At 2 warns: time them out for 1h" in client.sent[-1]["content"]


def test_a_kick_rule_removes_the_member():
    client = setup()
    seed_member(client)
    say("!warnrule add 1 kick")
    say("!warn @ann bad")
    assert calls(client, "PUT", "/members/u-ann/removal")


def test_a_refused_escalation_names_the_missing_permission():
    client = setup()
    seed_member(client)
    say("!warnrule add 1 kick")
    client.respond("PUT", "/members/u-ann/removal", ApiError(403, {"error": "forbidden"}))
    say("!warn @ann bad")
    assert "Ban Members" in client.sent[-1]["content"]


def test_a_warn_needs_kick_members_and_a_mod_cannot_warn_themselves():
    client = setup()
    seed_member(client)
    say("!warn @ann nope", user=ANN, permissions=0)
    assert "Kick Members" in client.ephemerals[-1]["content"]
    assert not client.sent
    say("!warn @mod me")
    assert "another person" in client.ephemerals[-1]["content"]


def test_a_member_sees_their_own_warns_but_not_others():
    client = setup()
    seed_member(client)
    say("!warn @ann x")
    say("!warns", user=ANN, permissions=0)
    assert "1 active warn" in client.ephemerals[-1]["content"]
    say("!warns @mod", user=ANN, permissions=0)
    assert "Kick Members" in client.ephemerals[-1]["content"]


def warn_ann_then_fail_ephemeral(status, body):
    client = setup()
    seed_member(client)
    say("!warn @ann shared a private address")
    client.respond("POST", "/channels/c1/ephemeral-messages", ApiError(status, body))
    return client


def test_a_warns_listing_is_never_posted_publicly_when_the_command_was_deleted():
    client = warn_ann_then_fail_ephemeral(404, {"error": "message not found"})
    say("!warns @ann")
    assert not [m for m in client.sent if "private address" in m["content"]]


def test_a_warns_listing_is_never_posted_publicly_on_a_server_without_private_replies():
    client = warn_ann_then_fail_ephemeral(405, None)
    say("!warns @ann")
    assert not [m for m in client.sent if "private address" in m["content"]]


def test_expired_warns_stop_counting():
    client = setup(EXPIRE_DAYS=1)
    seed_member(client)
    say("!warn @ann old")
    asyncio.run(warns.bot.store.run(lambda c: c.execute("UPDATE warns SET created_at = created_at - 200000") and c.commit()))
    say("!warn @ann new")
    assert "1 active warn." in client.sent[-1]["content"]


def test_unwarn_removes_one_and_clear_removes_all():
    client = setup()
    seed_member(client)
    say("!warn @ann a")
    say("!warn @ann b")
    say("!unwarn 1")
    assert "removed warn #1" in client.ephemerals[-1]["content"]
    say("!unwarn 99")
    assert "no warn #99" in client.ephemerals[-1]["content"]
    say("!unwarn clear @ann")
    assert "cleared 1 warn " in client.ephemerals[-1]["content"]


def test_a_bad_rule_is_explained_and_rules_list():
    client = setup()
    say("!warnrule add three timeout 1h")
    assert "warnrule add" in client.ephemerals[-1]["content"]
    say("!warnrule add 3 timeout 1h")
    say("!warnrule list")
    assert "3 warns: time them out for 1h" in client.ephemerals[-1]["content"]
    say("!warnrule remove 3")
    say("!warnrule remove 3")
    assert "no rule" in client.ephemerals[-1]["content"]


def test_a_reason_cannot_ping():
    client = setup(LOG_CHANNEL="c-log")
    seed_member(client)
    say("!warn @ann @everyone hi")
    logged = next(s["content"] for s in client.sent if s["channel_id"] == "c-log")
    assert "\\@everyone" in logged
    assert logged.count("@everyone") == 1, "only the escaped one, never a live one"


def forget_warns():
    asyncio.run(warns.bot.store.run(warns.clear_warns, "u-ann"))


def refused_reply(client):
    return client.ephemerals[-1]["content"]


def test_a_kick_rule_needs_ban_members_from_the_moderator_not_only_from_the_bot():
    client = setup()
    seed_member(client)
    say("!warnrule add 1 kick")
    say("!warn @ann bad", permissions=Permissions.KICK_MEMBERS)
    assert "Ban Members" in refused_reply(client)
    assert not calls(client, "PUT", "/members/u-ann/removal")
    say("!warns @ann")
    assert "no active warns" in refused_reply(client), "a refused warn is not recorded"


def test_a_timeout_rule_needs_only_kick_members_from_the_moderator():
    client = setup()
    seed_member(client)
    say("!warnrule add 1 timeout 1h")
    say("!warn @ann bad", permissions=Permissions.KICK_MEMBERS)
    assert calls(client, "PUT", "/members/u-ann/timeout")


def test_a_rule_does_not_run_on_someone_who_holds_more_than_the_moderator():
    client = setup()
    seed_member(client, permissions=Permissions.KICK_MEMBERS | Permissions.MANAGE_ROLES)
    say("!warnrule add 1 timeout 1h")
    say("!warn @ann bad", permissions=Permissions.KICK_MEMBERS)
    assert "outranks" in refused_reply(client)
    assert not calls(client, "PUT", "/members/u-ann/timeout")


def test_equal_bits_are_no_bar_and_an_administrator_follows_the_ladder_like_anyone():
    client = setup()
    seed_member(client, permissions=Permissions.KICK_MEMBERS)
    say("!warnrule add 1 timeout 1h")
    say("!warn @ann bad", permissions=Permissions.KICK_MEMBERS)
    assert len(calls(client, "PUT", "/members/u-ann/timeout")) == 1, "a peer in bits strictly below on the ladder is in reach"
    forget_warns()
    seed_member(client, permissions=Permissions.ADMINISTRATOR)
    rank(client, ANN, 5)
    say("!warn @ann bad", permissions=Permissions.ADMINISTRATOR)
    assert "outranks" in refused_reply(client)
    assert len(calls(client, "PUT", "/members/u-ann/timeout")) == 1
    rank(client, ANN, 4)
    say("!warn @ann bad", permissions=Permissions.ADMINISTRATOR)
    assert len(calls(client, "PUT", "/members/u-ann/timeout")) == 2


def test_a_rule_does_not_run_on_a_member_above_the_moderator_on_the_ladder():
    client = setup()
    seed_member(client, permissions=0)
    rank(client, ANN, 7)
    say("!warnrule add 1 timeout 1h")
    say("!warn @ann bad", permissions=Permissions.KICK_MEMBERS)
    assert "outranks" in refused_reply(client)
    say("!warnrule add 1 kick")
    say("!warn @ann bad")
    assert "outranks" in refused_reply(client)
    assert not calls(client, "PUT", "/members/u-ann/timeout") and not calls(client, "PUT", "/members/u-ann/removal")
    say("!warns @ann")
    assert "no active warns" in refused_reply(client), "a refused warn is not recorded"


def test_the_owner_is_out_of_reach_and_reaches_everyone():
    client = setup()
    seed_member(client)
    say("!warnrule add 1 timeout 1h")
    rank(client, ANN, None, owner=True)
    say("!warn @ann bad")
    assert "outranks" in refused_reply(client) and not calls(client, "PUT", "/members/u-ann/timeout")
    rank(client, ANN, 9)
    seed_member(client)
    rank(client, MOD, None, owner=True)
    say("!warn @ann bad", permissions=Permissions.KICK_MEMBERS)
    assert calls(client, "PUT", "/members/u-ann/timeout")


def test_a_rank_the_server_does_not_report_refuses_the_rule():
    client = setup()
    seed_member(client)
    say("!warnrule add 1 timeout 1h")
    client.respond("GET", "/users/u-ann", {**ANN, "role_ids": []})
    say("!warn @ann bad")
    assert "cannot read" in refused_reply(client) and not calls(client, "PUT", "/members/u-ann/timeout")


def test_a_warn_that_triggers_no_rule_is_recorded_whatever_the_ranks():
    client = setup()
    seed_member(client, permissions=Permissions.ADMINISTRATOR)
    say("!warn @ann note", permissions=Permissions.KICK_MEMBERS)
    assert "1 active warn." in client.sent[-1]["content"]


def serve_permissions(client, user_id, bits):
    client.respond("GET", f"/members/{user_id}/permissions", {"user_id": user_id, "permissions": int(bits)})


def test_without_the_role_table_the_rank_is_read_from_the_permissions_route():
    client = setup()
    seed_member(client)
    say("!warnrule add 1 timeout 1h")
    warns.bot.space.roles_readable = False
    warns.bot.permissions_readable = False
    say("!warn @ann bad", permissions=Permissions.KICK_MEMBERS)
    assert "cannot read" in refused_reply(client) and not calls(client, "PUT", "/members/u-ann/timeout")
    warns.bot.permissions_readable = True
    serve_permissions(client, "u-mod", Permissions.KICK_MEMBERS)
    serve_permissions(client, "u-ann", Permissions.KICK_MEMBERS)
    say("!warn @ann bad")
    assert calls(client, "PUT", "/members/u-ann/timeout"), "equal bits strictly below on the ladder are in reach"
    forget_warns()
    rank(client, ANN, 5)
    say("!warn @ann bad")
    assert "outranks" in refused_reply(client), "an equal rank is still refused"
    rank(client, ANN, None)
    serve_permissions(client, "u-ann", Permissions.SEND_MESSAGES)
    serve_permissions(client, "u-mod", Permissions.KICK_MEMBERS | Permissions.SEND_MESSAGES)
    say("!warn @ann bad")
    assert len(calls(client, "PUT", "/members/u-ann/timeout")) == 2


def test_a_timed_out_member_cannot_be_ranked_without_the_role_table():
    client = setup()
    seed_member(client).timed_out_until = 4_000_000_000_000
    say("!warnrule add 1 timeout 1h")
    warns.bot.space.roles_readable = False
    warns.bot.permissions_readable = True
    serve_permissions(client, "u-mod", Permissions.KICK_MEMBERS)
    serve_permissions(client, "u-ann", 0)
    say("!warn @ann bad")
    assert "cannot read" in refused_reply(client) and not calls(client, "PUT", "/members/u-ann/timeout")


def without_role_read_and_peer_bits(client):
    warns.bot.space.roles_readable = False
    warns.bot.permissions_readable = True
    serve_permissions(client, "u-mod", Permissions.KICK_MEMBERS | Permissions.SEND_MESSAGES)
    serve_permissions(client, "u-ann", Permissions.KICK_MEMBERS)


def test_a_peer_timed_out_after_the_bot_connected_is_refused_from_the_timeout_frame():
    client = setup()
    seed_member(client)
    say("!warnrule add 1 timeout 1h")
    without_role_read_and_peer_bits(client)
    handle_frame(warns.bot, {"type": "member.timeout", "user_id": "u-ann", "until": 4_000_000_000_000})
    say("!warn @ann bad")
    assert "cannot read" in refused_reply(client) and not calls(client, "PUT", "/members/u-ann/timeout")


def test_a_lifted_or_lapsed_timeout_no_longer_blocks_the_rank_check():
    client = setup()
    seed_member(client).timed_out_until = 4_000_000_000_000
    say("!warnrule add 1 timeout 1h")
    say("!warnrule add 2 timeout 1h")
    without_role_read_and_peer_bits(client)
    handle_frame(warns.bot, {"type": "member.timeout", "user_id": "u-ann", "until": None})
    say("!warn @ann bad")
    assert calls(client, "PUT", "/members/u-ann/timeout"), "a lifted timeout still refused"
    seed_member(client).timed_out_until = 1_000
    serve_permissions(client, "u-ann", Permissions.KICK_MEMBERS)
    say("!warn @ann again")
    assert len(calls(client, "PUT", "/members/u-ann/timeout")) == 2, "a lapsed timeout still refused"


def test_the_space_owner_cannot_be_warned():
    client = setup()
    client.respond("GET", "/users/u-ann", {**ANN, "role_ids": [], "is_owner": True})
    asyncio.run(warns.bot.space.fetch_member("u-ann"))
    say("!warnrule add 1 timeout 1h")
    say("!warn @ann bad", permissions=Permissions.ADMINISTRATOR)
    assert "owner" in refused_reply(client)
    assert not calls(client, "PUT", "/members/u-ann/timeout")
    say("!warns @ann")
    assert "has no active warns" in client.ephemerals[-1]["content"]


def seed_warn(user_id, moderator_id="u-admin"):
    return asyncio.run(warns.bot.store.run(warns.add_warn, user_id, moderator_id, "seeded"))


def test_a_moderator_cannot_remove_their_own_warns():
    client = setup()
    seed_member(client)
    warn_id = seed_warn("u-mod")
    say(f"!unwarn {warn_id}")
    assert "your own" in refused_reply(client)
    say("!unwarn clear @mod")
    assert "your own" in refused_reply(client)
    assert len(asyncio.run(warns.bot.store.run(warns.active_warns, "u-mod"))) == 1
    rank(client, MOD, None, owner=True)
    say(f"!unwarn {warn_id}")
    assert f"removed warn #{warn_id}" in refused_reply(client), "the owner answers to nobody"


def test_unwarn_reaches_only_members_below_the_moderator():
    client = setup()
    seed_member(client)
    warn_id = seed_warn("u-ann")
    rank(client, ANN, 5)
    say(f"!unwarn {warn_id}")
    assert "outranks" in refused_reply(client)
    say("!unwarn clear @ann")
    assert "outranks" in refused_reply(client)
    assert len(asyncio.run(warns.bot.store.run(warns.active_warns, "u-ann"))) == 1
    rank(client, ANN, 4)
    say(f"!unwarn {warn_id}")
    assert f"removed warn #{warn_id}" in refused_reply(client)


def test_a_timeout_rule_longer_than_the_server_allows_is_refused_when_added():
    client = setup()
    say("!warnrule add 3 timeout 29d")
    assert "28d" in refused_reply(client)
    say("!warnrule add 3 timeout 28d")
    say("!warnrule list")
    assert "3 warns: time them out for 28d" in refused_reply(client)


def test_the_log_channel_gets_a_line():
    client = setup(LOG_CHANNEL="c-log")
    seed_member(client)
    say("!warn @ann logged")
    assert any(s["channel_id"] == "c-log" for s in client.sent)



def test_an_account_deletion_drops_its_warns_and_a_removal_keeps_them():
    seed_member(setup())
    say("!warn @ann first")
    handle_frame(warns.bot, {"type": "member.removed", "user_id": "u-ann"})
    assert len(asyncio.run(warns.bot.store.run(warns.active_warns, "u-ann"))) == 1
    handle_frame(warns.bot, {"type": "member.removed", "user_id": "u-ann", "deleted": True})
    assert asyncio.run(warns.bot.store.run(warns.active_warns, "u-ann")) == []


if __name__ == "__main__":
    run_module_tests(globals())
