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
from slimbots.testing import FakeAsyncClient, human, run_module_tests, wire_bot  # noqa: E402

MOD = human("u-mod", "mod", "Mod")
ANN = human("u-ann", "ann", "Ann")
_counter = [0]


def setup(**config):
    for name, value in {"LOG_CHANNEL": "", "EXPIRE_DAYS": 0, **config}.items():
        setattr(warns, name, value)
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/users/u-mod", {**MOD, "role_ids": []})
    client.respond("GET", "/users/u-ann", {**ANN, "role_ids": []})
    wire_bot(warns.bot, client, db_name="warns.db")
    asyncio.run(warns.bot.space.refresh_members())
    asyncio.run(warns.bot.open_store(migrate=warns.init_db))
    client.respond("PUT", "/members/u-ann/removal", None)
    return client


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


def test_a_rule_does_not_run_on_an_equal_peer_unless_the_moderator_is_an_administrator():
    client = setup()
    seed_member(client, permissions=Permissions.KICK_MEMBERS)
    say("!warnrule add 1 timeout 1h")
    say("!warn @ann bad", permissions=Permissions.KICK_MEMBERS)
    assert "outranks" in refused_reply(client)
    assert not calls(client, "PUT", "/members/u-ann/timeout")
    seed_member(client, permissions=Permissions.ADMINISTRATOR)
    say("!warn @ann bad", permissions=Permissions.ADMINISTRATOR)
    assert calls(client, "PUT", "/members/u-ann/timeout")


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
    assert "outranks" in refused_reply(client), "an equal peer is still refused"
    serve_permissions(client, "u-ann", Permissions.SEND_MESSAGES)
    serve_permissions(client, "u-mod", Permissions.KICK_MEMBERS | Permissions.SEND_MESSAGES)
    say("!warn @ann bad")
    assert calls(client, "PUT", "/members/u-ann/timeout")


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


def test_the_log_channel_gets_a_line():
    client = setup(LOG_CHANNEL="c-log")
    seed_member(client)
    say("!warn @ann logged")
    assert any(s["channel_id"] == "c-log" for s in client.sent)


if __name__ == "__main__":
    run_module_tests(globals())
