#!/usr/bin/env python3
"""Tests against FakeAsyncClient, no server needed; run directly: python3 test_bot.py."""

import asyncio

import bot as tickets
import ticketdb
from slimbots.http import ApiError
from slimbots.models import Channel
from slimbots.permissions import Permissions
from slimbots.testing import FakeAsyncClient, run_module_tests, wire_bot

STAFF_ROLE = {"id": "r-staff", "name": "Support", "permissions": 0}
PEOPLE = {
    "u-1": {"id": "u-1", "username": "sam", "display_name": "Sam", "role_ids": []},
    "u-2": {"id": "u-2", "username": "ana", "display_name": "Ana", "role_ids": []},
    "s-1": {"id": "s-1", "username": "kai", "display_name": "Kai", "role_ids": ["r-staff"]},
}
CHANNEL = {"id": "t-1", "name": "ticket-0001"}


def setup(*, staff_role="Support"):
    """A fresh store and a fake server per test, so nothing leaks from one test into the next."""
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/roles", [STAFF_ROLE])
    client.respond("GET", "/categories", [])
    client.respond("POST", "/channels", CHANNEL)
    client.respond("PUT", f"/channels/{CHANNEL['id']}/overwrites", {})
    client.respond("DELETE", f"/channels/{CHANNEL['id']}", None)
    client.respond("PATCH", f"/channels/{CHANNEL['id']}", CHANNEL)
    tickets.STAFF_ROLE = staff_role
    tickets.CATEGORY = ""
    tickets.MAX_TRANSCRIPT = 100000
    wire_bot(tickets.bot, client, db_name="tickets.db")
    asyncio.run(tickets.bot.space.refresh_roles())
    for person in PEOPLE.values():
        tickets.bot.space.members[person["id"]] = tickets.bot.space._make_member(person)
    asyncio.run(tickets.bot.open_store(migrate=ticketdb.init_db))
    return client


def press(custom_id, user_id="u-1", channel_id="c-panel"):
    frame = {
        "type": "interaction.created", "interaction_id": "i1", "channel_id": channel_id, "message_id": "m-1",
        "custom_id": custom_id, "user_id": user_id, "user_display_name": PEOPLE[user_id]["display_name"], "created_at": 1,
    }

    async def run():
        await tickets.bot._handle_frame(frame)
        await asyncio.gather(*list(tickets.bot._background_tasks))

    asyncio.run(run())


def say(client, text, *, user_id="u-1", channel_id="c-panel", permissions=0):
    member = tickets.bot.space.members[user_id]
    member._base_permissions = permissions
    message = {"id": "m-say", "channel_id": channel_id, "author_id": user_id, "content": text, "seq": 1}
    asyncio.run(tickets.bot.process_message(message))


def rows_of(sql, *args):
    return asyncio.run(tickets.bot.store.run(lambda conn: conn.execute(sql, args).fetchall()))


def history(*lines):
    return [
        {"id": f"h{i}", "seq": i, "created_at": 1_700_000_000_000 + i * 1000, "author_display_name": who, "content": text}
        for i, (who, text) in enumerate(lines, 1)
    ]


def open_one(client, user_id="u-1"):
    press("ticket:open", user_id)
    return client.sent[-1]


def test_the_panel_command_posts_an_open_button_and_needs_manage_server():
    client = setup()
    say(client, "!ticket panel")
    assert not client.sent
    say(client, "!ticket panel", permissions=Permissions.MANAGE_SERVER)
    assert [b["custom_id"] for b in client.sent[-1]["components"][0]["buttons"]] == ["ticket:open"]


def test_opening_makes_a_restricted_channel_only_the_opener_and_staff_can_see():
    client = setup()
    welcome = open_one(client)
    created = next(c for c in client.calls if c[:2] == ("POST", "/channels"))
    assert created[2]["restricted"] is True and created[2]["name"] == "ticket-0001"
    grants = next(c for c in client.calls if c[1].endswith("/overwrites"))[2]["overwrites"]
    assert {(g["kind"], g["id"]) for g in grants} == {("member", "u-1"), ("role", "r-staff")}
    assert all(g["allow"] & Permissions.VIEW_CHANNEL for g in grants)
    assert welcome["channel_id"] == "t-1"
    assert [b["custom_id"] for b in welcome["components"][0]["buttons"]] == ["ticket:claim", "ticket:close"]
    assert "ticket-0001" in client.ephemerals[-1]["content"]


def test_a_second_press_by_the_same_member_points_at_the_existing_ticket():
    client = setup()
    open_one(client)
    tickets.bot.space.channels["t-1"] = Channel(CHANNEL)
    press("ticket:open")
    creates = [c for c in client.calls if c[:2] == ("POST", "/channels")]
    assert len(creates) == 1
    assert "already have an open ticket: #ticket-0001" in client.ephemerals[-1]["content"]


def test_another_member_gets_their_own_ticket():
    client = setup()
    open_one(client)
    client.respond("POST", "/channels", {"id": "t-2", "name": "ticket-0002"})
    client.respond("PUT", "/channels/t-2/overwrites", {})
    press("ticket:open", "u-2")
    assert len([c for c in client.calls if c[:2] == ("POST", "/channels")]) == 2


def test_opening_without_a_staff_role_configured_is_refused_and_creates_nothing():
    client = setup(staff_role="")
    press("ticket:open")
    assert "TICKETS_STAFF_ROLE" in client.ephemerals[-1]["content"]
    assert not [c for c in client.calls if c[:2] == ("POST", "/channels")]


def test_a_failed_channel_create_frees_the_members_slot():
    client = setup()
    client.respond("POST", "/channels", ApiError(403, {}))
    press("ticket:open")
    assert "Manage Channels" in client.ephemerals[-1]["content"]
    client.respond("POST", "/channels", CHANNEL)
    press("ticket:open")
    assert client.sent[-1]["channel_id"] == "t-1"


def test_a_failed_overwrite_deletes_the_half_made_channel():
    client = setup()
    client.respond("PUT", "/channels/t-1/overwrites", ApiError(403, {}))
    press("ticket:open")
    assert ("DELETE", "/channels/t-1") in [c[:2] for c in client.calls]
    assert not client.sent


def test_a_non_staff_member_cannot_claim_or_close():
    client = setup()
    open_one(client)
    press("ticket:claim", "u-2", "t-1")
    assert "only ticket staff" in client.ephemerals[-1]["content"]
    press("ticket:close", "u-1", "t-1")
    assert "only ticket staff" in client.ephemerals[-1]["content"]
    assert rows_of("SELECT status, claimed_by FROM tickets") == [("open", None)]
    assert not rows_of("SELECT * FROM transcripts")


def test_staff_claim_disables_the_claim_button_and_a_second_claim_is_refused():
    client = setup()
    open_one(client)
    press("ticket:claim", "s-1", "t-1")
    assert rows_of("SELECT claimed_by FROM tickets") == [("s-1",)]
    assert client.component_edits[-1]["components"][0]["buttons"][0]["disabled"] is True
    assert "claimed this ticket" in client.sent[-1]["content"]
    tickets.bot.space.members["u-2"].role_ids = ["r-staff"]
    press("ticket:claim", "u-2", "t-1")
    assert "already claimed by Kai" in client.ephemerals[-1]["content"]


def test_a_manage_server_member_counts_as_staff():
    client = setup()
    open_one(client)
    tickets.bot.space.members["u-2"]._base_permissions = Permissions.MANAGE_SERVER
    press("ticket:claim", "u-2", "t-1")
    assert rows_of("SELECT claimed_by FROM tickets") == [("u-2",)]


def test_close_saves_a_transcript_then_deletes_the_channel():
    client = setup()
    open_one(client)
    messages = history(("Sam", "my printer is on fire"), ("Kai", "line one\nline two"))
    client.respond("GET", "/channels/t-1/messages", list(reversed(messages)))
    press("ticket:close", "s-1", "t-1")
    status, body, truncated = rows_of("SELECT t.status, x.body, x.truncated FROM tickets t JOIN transcripts x ON x.ticket_number = t.number")[0]
    assert status == "closed" and truncated == 0
    lines = body.split("\n")
    assert lines[0].endswith("UTC] Sam: my printer is on fire")
    assert lines[1].endswith("UTC] Kai: line one") and lines[2] == "    line two"
    assert ("DELETE", "/channels/t-1") in [c[:2] for c in client.calls]


def test_a_failed_history_read_keeps_the_ticket_open():
    client = setup()
    open_one(client)
    client.respond("GET", "/channels/t-1/messages", ApiError(500, {}))
    press("ticket:close", "s-1", "t-1")
    assert rows_of("SELECT status FROM tickets") == [("open",)]
    assert ("DELETE", "/channels/t-1") not in [c[:2] for c in client.calls]


def test_a_closed_member_can_open_a_new_ticket():
    client = setup()
    open_one(client)
    client.respond("GET", "/channels/t-1/messages", [])
    press("ticket:close", "s-1", "t-1")
    client.respond("POST", "/channels", {"id": "t-2", "name": "ticket-0002"})
    client.respond("PUT", "/channels/t-2/overwrites", {})
    press("ticket:open")
    assert client.sent[-1]["channel_id"] == "t-2"


def test_the_transcript_is_cut_at_the_configured_size():
    text, truncated = ticketdb.build_transcript(history(("Sam", "x" * 500)), 100)
    assert truncated and len(text) == 100


def test_transcript_lookup_is_staff_only_paged_and_private():
    client = setup()
    open_one(client)
    client.respond("GET", "/channels/t-1/messages", history(("Sam", "y" * 2500)))
    press("ticket:close", "s-1", "t-1")
    say(client, "!ticket transcript 1", user_id="u-2")
    assert "only ticket staff" in client.ephemerals[-1]["content"]
    say(client, "!ticket transcript 1", user_id="s-1")
    assert "page 1 of 2" in client.ephemerals[-1]["content"] and "from Sam" in client.ephemerals[-1]["content"]
    say(client, "!ticket transcript 1 2", user_id="s-1")
    assert "page 2 of 2" in client.ephemerals[-1]["content"]
    say(client, "!ticket transcript 9", user_id="s-1")
    assert "no saved transcript" in client.ephemerals[-1]["content"]
    assert not [m for m in client.sent if "page" in m["content"]]


def test_reason_is_limited_and_updates_the_topic():
    client = setup()
    open_one(client)
    say(client, "!ticket reason " + "x" * 300, channel_id="t-1")
    assert "1 to 200" in client.ephemerals[-1]["content"]
    say(client, "!ticket reason  printer   on fire", channel_id="t-1")
    assert rows_of("SELECT reason FROM tickets") == [("printer on fire",)]
    assert next(c for c in client.calls if c[0] == "PATCH")[2] == {"topic": "printer on fire"}
    say(client, "!ticket reason hi", user_id="u-2", channel_id="t-1")
    assert "only the ticket's opener" in client.ephemerals[-1]["content"]


if __name__ == "__main__":
    run_module_tests(globals())
