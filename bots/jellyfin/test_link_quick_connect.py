#!/usr/bin/env python3
"""`!jellyfin link` proves the account with a Quick Connect code: the code stays private, the approver is confirmed, and only then is a link verified.
Run directly: python3 test_link_quick_connect.py."""

import asyncio
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import accounts  # noqa: E402
import jellyfin_core  # noqa: E402
import quick_connect  # noqa: E402
from slimbots import ApiError  # noqa: E402
from slimbots.web import ServiceHTTPError, ServiceUnreachable  # noqa: E402
from test_bot import asynced, jellyfin, message, setup  # noqa: E402
from test_resume import Patched  # noqa: E402


class FakeJellyfin:
    """Quick Connect as the bot sees it: a code, polls that turn approved after `approve_after`, and the approving user."""

    def __init__(self, approve_after=1, user=("jf-a", "Alice"), initiate_error=None, poll_error=None, redeem_error=None):
        self.approve_after, self.user = approve_after, user
        self.initiate_error, self.poll_error, self.redeem_error = initiate_error, poll_error, redeem_error
        self.initiated, self.polls, self.redeemed = [], 0, 0

    async def initiate(self, member_name):
        if self.initiate_error:
            raise self.initiate_error
        self.initiated.append(member_name)
        return quick_connect.Pending(code="482913", secret="s3cret", device_id="dev-1", device_name=quick_connect.device_name(member_name))

    async def is_approved(self, pending):
        self.polls += 1
        if self.poll_error:
            raise self.poll_error
        return self.approve_after is not None and self.polls >= self.approve_after

    async def redeem(self, pending):
        self.redeemed += 1
        if self.redeem_error:
            raise self.redeem_error
        return quick_connect.Approved(*self.user)

    def patched(self, lifetime=0.2):
        return Patched(
            (quick_connect, "initiate", self.initiate), (quick_connect, "is_approved", self.is_approved), (quick_connect, "redeem", self.redeem),
            (accounts, "POLL_SECONDS", 0.005), (accounts, "CODE_LIFETIME_SECONDS", lifetime),
        )


def fresh():
    client = setup()
    accounts._flows.clear()
    jellyfin.bot._fatal_error = None
    return client


def say(*contents, author="u1"):
    """Each message in turn, letting the poll it starts finish inside the same loop before the next one."""

    async def go():
        for n, content in enumerate(contents):
            msg = {**message(content, msg_id=f"q{len(contents)}-{n}-{content}"), "author_id": author}
            await jellyfin.bot.process_message(msg)
            jellyfin_core._command_cooldown._last.clear()
            while jellyfin.bot._background_tasks:
                await asyncio.gather(*list(jellyfin.bot._background_tasks))

    asyncio.run(go())


def private(client):
    return [m["content"] for m in client.ephemerals]


def link_of(user, verified=False):
    lookup = accounts.verified_link if verified else accounts.get_link
    return asyncio.run(jellyfin.bot.store.run(lookup, user))


def test_the_code_is_sent_privately_and_the_device_names_the_member():
    client, jf = fresh(), FakeJellyfin(approve_after=None)
    with jf.patched():
        say("!jellyfin link")
    assert "your code is 482913" in private(client)[0] and "Only enter a code you asked for yourself" in private(client)[0]
    assert not any("482913" in m.get("content", "") for m in client.sent)
    assert jf.initiated == ["Nick"]


def test_an_approval_asks_the_member_to_confirm_who_approved_it_and_links_nothing_yet():
    client, jf = fresh(), FakeJellyfin()
    with jf.patched():
        say("!jellyfin link")
    assert 'approved by the jellyfin user "Alice"' in private(client)[-1] and "`!jellyfin link confirm`" in private(client)[-1]
    assert link_of("u1") is None and jf.redeemed == 1


def test_confirming_links_the_approver_as_a_verified_link():
    client, jf = fresh(), FakeJellyfin()
    with jf.patched():
        say("!jellyfin link", "!jellyfin link confirm")
    assert link_of("u1", verified=True) == ("jf-a", "Alice")
    assert private(client)[-1].startswith('linked as "Alice"')


def test_confirm_with_nothing_approved_is_refused():
    client = fresh()
    say("!jellyfin link confirm")
    assert "there is no approved code to confirm" in private(client)[-1] and link_of("u1") is None


def test_a_stale_approval_cannot_be_confirmed():
    client, jf = fresh(), FakeJellyfin()
    with jf.patched(), Patched((accounts, "CONFIRM_SECONDS", -1.0)):
        say("!jellyfin link", "!jellyfin link confirm")
    assert link_of("u1") is None and "there is no approved code to confirm" in private(client)[-1]


def test_an_unapproved_code_expires_and_a_new_one_can_be_asked_for():
    client, jf = fresh(), FakeJellyfin(approve_after=None)
    with jf.patched(lifetime=0.05):
        say("!jellyfin link")
        assert "the code expired without being approved" in private(client)[-1]
        say("!jellyfin link")
    assert len(jf.initiated) == 2 and link_of("u1") is None


def test_jellyfin_forgetting_the_code_ends_the_wait_as_expired():
    client = fresh()
    jf = FakeJellyfin(poll_error=ServiceHTTPError("gone", 404))
    with jf.patched(lifetime=5.0):
        say("!jellyfin link")
    assert jf.polls == 1 and "expired" in private(client)[-1]


def test_asking_again_while_a_code_waits_repeats_it_rather_than_issuing_another():
    client, jf = fresh(), FakeJellyfin(approve_after=None)

    async def go():
        with jf.patched(lifetime=0.3):
            await jellyfin.bot.process_message(message("!jellyfin link", msg_id="a1"))
            await jellyfin.bot.process_message(message("!jellyfin link", msg_id="a2"))
            await jellyfin.bot.process_message(message("!jellyfin link cancel", msg_id="a3"))
            await asyncio.gather(*list(jellyfin.bot._background_tasks))

    asyncio.run(go())
    assert jf.initiated == ["Nick"]
    assert private(client)[1] == "you already have a code waiting: 482913."
    assert private(client)[2] == "cancelled." and len(private(client)) == 3


def test_an_approval_after_cancelling_is_ignored():
    client, jf = fresh(), FakeJellyfin(approve_after=3)

    async def go():
        with jf.patched(lifetime=5.0):
            await jellyfin.bot.process_message(message("!jellyfin link", msg_id="c1"))
            await jellyfin.bot.process_message(message("!jellyfin link cancel", msg_id="c2"))
            await asyncio.gather(*list(jellyfin.bot._background_tasks))

    asyncio.run(go())
    assert jf.redeemed == 1 and not accounts._flows
    assert private(client)[-1] == "cancelled." and not any("approved by" in m for m in private(client))


def test_an_approver_already_linked_to_another_member_cannot_be_confirmed():
    client, jf = fresh(), FakeJellyfin()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u2", "jf-a", "Alice", True))
    with jf.patched():
        say("!jellyfin link", "!jellyfin link confirm")
    assert link_of("u1") is None and link_of("u2") == ("jf-a", "Alice")
    assert "already linked to someone else" in private(client)[-1]


def test_two_members_confirming_one_jellyfin_user_at_once_leave_one_owner():
    fresh()

    async def both():
        store = jellyfin.bot.store
        return await asyncio.gather(
            store.run(accounts.claim_link, "u1", "jf-a", "Alice", True), store.run(accounts.claim_link, "u2", "jf-a", "Alice", True)
        )

    assert sorted(asyncio.run(both())) == [False, True]
    owners = jellyfin.bot.store.connection.execute("SELECT slimm_user_id FROM user_links WHERE jellyfin_user_id = 'jf-a'").fetchall()
    assert len(owners) == 1, owners


def test_quick_connect_turned_off_says_so_and_issues_no_code():
    client = fresh()
    with FakeJellyfin(initiate_error=quick_connect.QuickConnectDisabled()).patched():
        say("!jellyfin link")
    assert [m["content"] for m in client.sent] == [
        "linking needs Quick Connect, which is turned off on this jellyfin server. A jellyfin admin can turn it on in the dashboard."
    ]
    assert not client.ephemerals and not accounts._flows


def test_jellyfin_down_when_starting_says_so():
    client = fresh()
    with FakeJellyfin(initiate_error=ServiceUnreachable("refused")).patched():
        say("!jellyfin link")
    assert [m["content"] for m in client.sent] == ["jellyfin is unavailable right now."]


def test_a_server_without_private_replies_never_shows_the_code():
    client = fresh()
    client.respond("POST", "/channels/c1/ephemeral-messages", ApiError(404, "not found"))
    with FakeJellyfin().patched():
        say("!jellyfin link")
    assert not any("482913" in m.get("content", "") for m in client.sent)
    assert "the code has to stay private" in client.sent[-1]["content"] and not accounts._flows


def test_a_broken_redeem_ends_the_link_quietly_and_leaves_the_bot_running():
    client, jf = fresh(), FakeJellyfin(redeem_error=KeyError("User"))
    with jf.patched():
        say("!jellyfin link")
    assert jellyfin.bot._fatal_error is None and not accounts._flows and link_of("u1") is None
    assert len(private(client)) == 1


def test_a_name_only_link_is_not_verified_and_account_says_how_to_confirm_it():
    client = fresh()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice"))
    say("!jellyfin account")
    assert link_of("u1", verified=True) is None
    assert 'linked to "Alice" by name only' in private(client)[-1]


def test_account_refreshes_a_renamed_jellyfin_user():
    client = fresh()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice", True))
    with Patched((jellyfin_core, "jf_get", asynced(lambda path, params=None: {"Id": "jf-a", "Name": "Alicia"}))):
        say("!jellyfin account")
    assert private(client)[-1] == 'you are linked to the jellyfin user "Alicia".' and link_of("u1") == ("jf-a", "Alicia")


def test_account_unlinks_a_jellyfin_user_that_was_deleted():
    client = fresh()
    asyncio.run(jellyfin.bot.store.run(accounts.set_link, "u1", "jf-a", "Alice", True))

    async def gone(path, params=None):
        raise ServiceHTTPError("not found", 404)

    with Patched((jellyfin_core, "jf_get", gone)):
        say("!jellyfin account")
    assert link_of("u1") is None and "no longer exists" in private(client)[-1]


def test_an_older_database_gains_the_verified_flag_and_keeps_one_claim_per_jellyfin_user():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE user_links (slimm_user_id TEXT PRIMARY KEY, jellyfin_user_id TEXT NOT NULL, jellyfin_name TEXT NOT NULL, linked_at INTEGER NOT NULL);"
        "INSERT INTO user_links VALUES ('u2', 'jf-a', 'Alice', 200), ('u1', 'jf-a', 'Alice', 100), ('u3', 'jf-b', 'Bob', 50);"
    )
    jellyfin_core.init_db(conn)
    jellyfin_core.init_db(conn)
    assert accounts.get_link(conn, "u1") == ("jf-a", "Alice") and accounts.get_link(conn, "u2") is None
    assert accounts.verified_link(conn, "u3") is None and accounts.get_link(conn, "u3") == ("jf-b", "Bob")
    try:
        conn.execute("INSERT INTO user_links VALUES ('u4', 'jf-b', 'Bob', 1, 0)")
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("a second claim on one jellyfin user was accepted")


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
