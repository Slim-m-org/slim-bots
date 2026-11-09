#!/usr/bin/env python3
"""Command-and-event tests against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import sqlite3

import bot as modlog
import outbox
import render
from slimbots import ApiError, Store, style
from slimbots.testing import FakeAsyncClient, handle_frame, human, run_module_tests, wire_bot

MEMBERS = [human("u1", "nick", "Nick", role_ids=[], roles=[])]


def message(author_id, content, msg_id="m1"):
    return {"id": msg_id, "author_id": author_id, "channel_id": "c1", "content": content}


def setup():
    modlog.bot.store = Store(":memory:", migrate=modlog.init_db)
    asyncio.run(modlog.bot.store.open())
    modlog.bot.channels = {"c1"}
    modlog._last_roles.clear()
    modlog._role_names.clear()
    modlog._open_gap_notice = None
    modlog._last_notice_at = None
    modlog._role_bits.clear()
    modlog.bot.moderation_head = None
    modlog.MODERATION_SETTLE_SECONDS = 0
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/members", MEMBERS)
    client.respond("GET", "/roles", ApiError(403, "forbidden"))
    wire_bot(modlog.bot, client, me_id="bot-1")
    asyncio.run(modlog.bot.space.refresh_members())
    return client


def dispatch_frame(frame):
    handle_frame(modlog.bot, frame)


def process(client, *messages):
    async def run():
        for msg in messages:
            await modlog.bot.process_message(msg)

    asyncio.run(run())


def last_embed(client):
    return client.sent[-1]["embeds"][0]


def field_values(client):
    return {f["name"]: f["value"] for f in last_embed(client).get("fields", [])}


def test_member_timeout_is_logged():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.timeout", "user_id": "u1", "until": 9999999999000})
    assert last_embed(client)["title"] == "Member timed out"
    assert field_values(client)["Member"] == "Nick (@nick)"
    assert field_values(client)["Until"].startswith("until 2286-")


def test_member_timeout_lifted_is_logged():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.timeout", "user_id": "u1", "until": None})
    assert last_embed(client)["title"] == "Timeout lifted"
    assert field_values(client) == {"Member": "Nick (@nick)"}


def test_member_removed_is_logged():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.removed", "user_id": "u1"})
    assert last_embed(client)["title"] == "Member removed"
    assert field_values(client) == {"Member": "Nick (@nick)"}
    assert client.sent[-1]["content"] == "Member removed: member Nick (@nick)"


def test_member_restored_is_logged():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.restored", "user_id": "u1"})
    assert last_embed(client)["title"] == "Member restored"


def test_an_unknown_member_is_never_shown_as_an_id():
    client = setup()
    gone = "0190aaaa-0000-7000-8000-000000000001"
    client.respond("GET", f"/users/{gone}", ApiError(404, "not found"))
    dispatch_frame({"type": "member.removed", "user_id": gone})
    assert field_values(client) == {"Member": "Unknown member"}


def test_role_grant_from_a_current_server_names_the_role():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0], "role_ids": ["r1"], "roles": ["Helper"]})
    dispatch_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "r1", "granted": True, "role_name": "Helper"})
    assert last_embed(client)["title"] == "Role granted"
    assert field_values(client) == {"Member": "Nick (@nick)", "Role": "Helper"}


def test_role_revoke_from_a_current_server():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "r1", "granted": False, "role_name": "Helper"})
    assert last_embed(client)["title"] == "Role revoked"


def test_role_change_from_an_older_server_is_inferred_from_the_member():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0], "role_ids": [], "roles": []})
    dispatch_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "r1"})
    assert last_embed(client)["title"] == "Member roles changed"

    client.respond("GET", "/users/u1", {**MEMBERS[0], "role_ids": ["r1"], "roles": ["Helper"]})
    dispatch_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "r1"})
    assert last_embed(client)["title"] == "Role granted"
    assert field_values(client)["Role"] == "Helper"

    client.respond("GET", "/users/u1", {**MEMBERS[0], "role_ids": [], "roles": []})
    dispatch_frame({"type": "member.role_changed", "user_id": "u1", "role_id": "r1"})
    assert last_embed(client)["title"] == "Role revoked"


def two_role_changes_with_a_slow_first_post():
    client = setup()
    posts = []
    plain_call = client.call

    async def call(method, path, body=None, **kw):
        if (method, path) == ("POST", "/channels/c1/messages"):
            posts.append(1)
            await asyncio.sleep(0.05 if len(posts) == 1 else 0)
        return await plain_call(method, path, body, **kw)

    client.call = call
    client.respond("GET", "/users/u1", {**MEMBERS[0], "role_ids": ["r1"], "roles": ["Helper"]})
    granted = {"type": "member.role_changed", "user_id": "u1", "role_id": "r1", "granted": True, "role_name": "Helper"}

    async def run():
        await modlog.bot._handle_frame(granted)
        await modlog.bot._handle_frame({**granted, "granted": False})
        await asyncio.gather(*list(modlog.bot._background_tasks))

    asyncio.run(run())
    return [sent["embeds"][0]["title"] for sent in client.sent]


def test_role_changes_that_arrive_together_are_logged_in_the_order_they_happened():
    assert two_role_changes_with_a_slow_first_post() == ["Role granted", "Role revoked"]


def test_the_log_would_swap_those_two_entries_if_handlers_were_not_ordered():
    modlog.bot.ordered_events = False
    try:
        assert two_role_changes_with_a_slow_first_post() == ["Role revoked", "Role granted"]
    finally:
        modlog.bot.ordered_events = True


def test_role_created_shows_its_name():
    client = setup()
    after = {"name": "Helpers", "color": 1, "hoist": False, "mentionable": False}
    dispatch_frame({"type": "role.changed", "role_id": "r1", "change": "created", "name": "Helpers", "after": after})
    assert last_embed(client)["title"] == "Role created"
    assert field_values(client) == {"Role": "Helpers"}


def test_role_renamed_and_recoloured_shows_each_difference():
    client = setup()
    before = {"name": "Mods", "color": None, "hoist": False, "mentionable": False}
    after = {"name": "Moderators", "color": 3, "hoist": True, "mentionable": False}
    dispatch_frame({"type": "role.changed", "role_id": "r1", "change": "updated", "name": "Moderators", "before": before, "after": after})
    assert last_embed(client)["title"] == "Role changed"
    assert field_values(client) == {
        "Role": "Moderators",
        "Name": "Mods -> Moderators",
        "Colour": "Automatic -> Violet",
        "Listed separately": "off -> on",
    }


def test_role_deleted_keeps_its_name():
    client = setup()
    dispatch_frame({"type": "role.changed", "role_id": "r1", "change": "deleted", "name": "Helpers", "before": {"name": "Helpers"}})
    assert last_embed(client)["title"] == "Role deleted"
    assert field_values(client) == {"Role": "Helpers"}


def test_role_permissions_diff_when_the_role_list_is_readable():
    client = setup()
    manage_messages, kick = 1 << 3, 1 << 6
    client.respond("GET", "/roles", [{"id": "r1", "permissions": manage_messages | kick}])
    asyncio.run(modlog.on_ready())
    client.respond("GET", "/roles", [{"id": "r1", "permissions": manage_messages | (1 << 7)}])
    dispatch_frame({"type": "role.changed", "role_id": "r1", "change": "updated", "name": "Mods", "before": {"name": "Mods"}, "after": {"name": "Mods"}})
    assert field_values(client)["Permissions"] == "+ Ban members, - Kick members"


def test_role_change_without_details_is_still_readable():
    client = setup()
    dispatch_frame({"type": "role.changed", "role_id": "r-unknown"})
    assert last_embed(client)["title"] == "Role changed"
    assert field_values(client) == {"Role": "Unknown role"}
    assert last_embed(client)["description"] == "Its position or access was adjusted."


def test_no_post_carries_an_id_a_route_or_an_event_type():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0], "role_ids": ["r1"], "roles": ["Helper"]})
    for frame in (
        {"type": "member.removed", "user_id": "u1"},
        {"type": "member.timeout", "user_id": "u1", "until": None},
        {"type": "member.role_changed", "user_id": "u1", "role_id": "r1", "granted": True, "role_name": "Helper"},
        {"type": "role.changed", "role_id": "r1"},
    ):
        dispatch_frame(frame)
    for sent in client.sent:
        text = sent["content"] + str(sent["embeds"])
        for banned in ("r1", "GET /", "MANAGE_", ".removed", ".changed", ".timeout", "footer"):
            assert banned not in text, (banned, text)


def test_permission_diff_names_what_was_added_and_removed():
    before = (1 << 3) | (1 << 6)
    after = (1 << 3) | (1 << 19)
    assert render.permission_diff(before, after) == "+ Move members, - Kick members"
    assert render.permission_diff(before, before) is None


def test_every_server_permission_bit_has_a_label():
    assert sorted(render.PERMISSION_LABELS) == list(range(20))


def test_colour_names_follow_the_client_palette():
    assert [render.colour_name(i) for i in (0, 9, None, 10)] == ["Gold", "Teal", "Automatic", "Automatic"]


def test_look_diff_ignores_what_did_not_change():
    same = {"name": "A", "color": 2, "hoist": False, "mentionable": True}
    assert render.role_look_diff(same, dict(same)) == []
    assert render.role_look_diff(None, same) == []


def test_modlog_stats_reports_nothing_recorded_yet():
    client = setup()
    process(client, message("u1", "!modlog stats"))
    assert client.sent[-1]["content"] == "nothing recorded yet."


def test_modlog_stats_counts_logged_events():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.removed", "user_id": "u1"})
    process(client, message("u1", "!modlog stats"))
    assert "member.removed: 1" in client.sent[-1]["content"]


def test_modlog_gaps_reports_none_recorded():
    client = setup()
    process(client, message("u1", "!modlog gaps"))
    assert client.sent[-1]["content"] == "no reconnect gaps recorded."


def test_modlog_permissions_names_the_one_bit():
    client = setup()
    process(client, message("u1", "!modlog permissions"))
    assert "View moderation history" in client.sent[-1]["content"]
    assert "GET /" not in client.sent[-1]["content"]


def test_modlog_bad_subcommand_is_a_clear_reply():
    client = setup()
    process(client, message("u1", "!modlog nonsense"))
    assert "`sub` must be one of: stats, gaps, permissions" in client.ephemerals[-1]["content"]


def test_reconnect_gap_is_recorded_and_reported():
    setup()
    modlog._last_seen_at = None
    asyncio.run(modlog.report_reconnect_gap())
    assert modlog.bot.store.connection.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 0

    modlog.note_alive()
    modlog._last_seen_at -= 4000
    client = modlog.bot.client
    asyncio.run(modlog.report_reconnect_gap())
    assert client.sent[-1]["content"] == "Mod log was offline for about 1h6m; events in that time were not recorded."
    assert "embeds" not in client.sent[-1]
    assert modlog.bot.store.connection.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 1


def test_a_blip_is_recorded_but_not_posted():
    setup()
    modlog.note_alive()
    modlog._last_seen_at -= 60
    client = modlog.bot.client
    asyncio.run(modlog.report_reconnect_gap())
    assert client.sent == []
    assert modlog.bot.store.connection.execute("SELECT COUNT(*) FROM gaps").fetchone()[0] == 1


def reconnect_after(seconds):
    modlog.note_alive()
    modlog._last_seen_at -= seconds
    asyncio.run(modlog.report_reconnect_gap())


def test_back_to_back_gaps_edit_one_notice():
    client = setup()
    reconnect_after(600)
    first_id = client.sent[-1]["id"]
    client.respond("PATCH", f"/channels/{modlog.bot.channel}/messages/{first_id}", {})
    reconnect_after(900)
    assert len(client.sent) == 1
    method, path, body, _ = client.calls[-1]
    assert (method, path) == ("PATCH", f"/channels/{modlog.bot.channel}/messages/{first_id}")
    assert body["content"] == "Mod log was offline for about 25m in total; events in that time were not recorded."


def test_a_gap_right_after_a_notice_and_a_log_line_is_not_repeated():
    client = setup()
    reconnect_after(600)
    asyncio.run(modlog.post("member.role_changed", "Role granted"))
    reconnect_after(600)
    assert len(client.sent) == 2


def test_a_gap_after_the_cooldown_gets_a_fresh_notice():
    client = setup()
    reconnect_after(600)
    asyncio.run(modlog.post("member.role_changed", "Role granted"))
    modlog._last_notice_at -= modlog.GAP_NOTICE_COOLDOWN_SECONDS + 1
    reconnect_after(600)
    assert len(client.sent) == 3


def test_a_short_gap_is_not_reported():
    setup()
    modlog.note_alive()
    client = modlog.bot.client
    asyncio.run(modlog.report_reconnect_gap())
    assert client.sent == []


def stored_moderation_seq():
    return modlog.bot.store.connection.execute("SELECT last_seq FROM moderation_state").fetchone()


def stored_build():
    return modlog.bot.store.connection.execute("SELECT build FROM moderation_state").fetchone()[0]


def restart_gap_count():
    return modlog.bot.store.connection.execute("SELECT COUNT(*) FROM moderation_gaps WHERE after_restart = 1").fetchone()[0]


def moderation_gap_count():
    return modlog.bot.store.connection.execute("SELECT COUNT(*) FROM moderation_gaps").fetchone()[0]


def serve_version(version, capabilities=("push",), build_id=None):
    body = {"version": version, "capabilities": list(capabilities)}
    if build_id is not None:
        body["build_id"] = build_id
    modlog.bot.client.respond("GET", "/version", body)


def ready_with_head(head, version="0.76.0"):
    serve_version(version)
    modlog.bot.moderation_head = head

    async def run():
        await modlog.check_moderation_cursor()
        while modlog.bot._background_tasks:
            await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())


def ready_in_one_loop(*connects):
    async def run():
        for head, version in connects:
            serve_version(version)
            modlog.bot.moderation_head = head
            await modlog.check_moderation_cursor()
            while modlog.bot._background_tasks:
                await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())


def test_the_highest_moderation_seq_is_persisted():
    setup()
    for seq in (30, 10, 20):
        dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": seq})
    assert stored_moderation_seq() == (30,)


def test_a_frame_without_a_moderation_seq_is_not_persisted():
    setup()
    dispatch_frame({"type": "message.created", "seq": 99, "message": {}})
    assert stored_moderation_seq() is None


SEND = ("POST", "/channels/c1/messages")
REMOVED = {"type": "member.removed", "user_id": "u1", "seq": 40}


def unposted():
    return modlog.bot.store.connection.execute("SELECT COUNT(*) FROM unposted").fetchone()[0]


def retry_at(offset_seconds):
    real = modlog.time.time
    modlog.time.time = lambda: real() + offset_seconds
    try:
        asyncio.run(modlog.retry_unposted())
    finally:
        modlog.time.time = real


def refuse_posts(client):
    client.respond(*SEND, ApiError(403, {"error": "missing SEND_MESSAGES"}))


def allow_posts(client):
    client._responses.pop(SEND)


def posted_ids(client):
    return [sent["id"] for sent in client.sent]


def test_a_posted_frame_leaves_nothing_to_retry():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame(REMOVED)
    assert len(client.sent) == 1 and unposted() == 0


def test_a_post_the_channel_refuses_is_kept_and_posted_on_a_later_try():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    refuse_posts(client)
    dispatch_frame(REMOVED)
    assert stored_moderation_seq() == (40,) and unposted() == 1, "the cursor passed it, so the entry is held for a retry"
    allow_posts(client)
    retry_at(modlog.outbox.RETRY_BASE_SECONDS + 1)
    assert unposted() == 0
    assert client.sent[-1]["embeds"][0]["title"] == "Member removed"
    assert stored_moderation_seq() == (40,)


def test_a_failure_before_the_post_is_kept_too():
    client = setup()
    client.respond("GET", "/users/u1", ApiError(500, {"error": "boom"}))
    dispatch_frame(REMOVED)
    assert unposted() == 1 and not client.sent
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    retry_at(modlog.outbox.RETRY_BASE_SECONDS + 1)
    assert unposted() == 0 and client.sent[-1]["embeds"][0]["title"] == "Member removed"


def test_a_retry_waits_longer_after_each_failure():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    refuse_posts(client)
    dispatch_frame(REMOVED)
    base = modlog.outbox.RETRY_BASE_SECONDS
    retry_at(base - 5)
    assert len(client.sent) == 1, "not due yet"
    retry_at(base + 1)
    assert len(client.sent) == 2 and unposted() == 1
    retry_at(base + 1 + base)
    assert len(client.sent) == 2, "the second wait is twice as long"
    retry_at(base + 1 + 2 * base + 1)
    assert len(client.sent) == 3


def test_a_frame_keeps_its_message_id_so_a_repeat_post_is_the_same_message():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame(REMOVED)
    first = posted_ids(client)[-1]
    dispatch_frame(REMOVED)
    assert posted_ids(client)[-1] == first
    dispatch_frame({**REMOVED, "seq": 41})
    assert posted_ids(client)[-1] != first


def test_modlog_gaps_says_how_many_events_are_waiting_to_be_posted():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    refuse_posts(client)
    dispatch_frame(REMOVED)
    allow_posts(client)
    process(client, message("u1", "!modlog gaps"))
    assert "1 moderation event(s) could not be posted yet" in client.sent[-1]["content"]


def test_the_first_connect_seeds_the_cursor_without_a_marker():
    setup()
    ready_with_head(500)
    assert stored_moderation_seq() == (500,)
    assert moderation_gap_count() == 0
    assert stored_build() == "0.76.0+push"


def test_a_head_ahead_on_an_unchanged_build_logs_a_plain_gap_marker():
    setup()
    ready_with_head(50)
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    ready_with_head(200)
    assert moderation_gap_count() == 1
    assert restart_gap_count() == 0
    assert stored_moderation_seq() == (200,)
    kinds = modlog.bot.store.connection.execute("SELECT kind, text FROM events WHERE kind = 'gap'").fetchall()
    assert len(kinds) == 1 and "unchanged server build" in kinds[0][1]


def test_a_head_ahead_after_the_build_changed_is_a_restart_gap():
    setup()
    ready_with_head(50, version="0.76.0")
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    ready_with_head(200, version="0.77.0")
    assert moderation_gap_count() == 1
    assert restart_gap_count() == 1
    text = modlog.bot.store.connection.execute("SELECT text FROM events WHERE kind = 'gap'").fetchone()[0]
    assert text == "server restarted, events may have been missed"
    assert stored_build() == "0.77.0+push"


def test_a_redeploy_with_a_new_build_id_is_a_restart_gap():
    setup()
    serve_version("0.76.0", build_id="aaaaaaa")
    modlog.bot.moderation_head = 50
    asyncio.run(modlog.check_moderation_cursor())
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    serve_version("0.76.0", build_id="bbbbbbb")
    modlog.bot.moderation_head = 200

    async def run():
        await modlog.check_moderation_cursor()
        while modlog.bot._background_tasks:
            await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())
    assert moderation_gap_count() == 1 and restart_gap_count() == 1
    assert stored_build() == "0.76.0+push@bbbbbbb"


def test_the_same_build_id_stays_a_plain_gap():
    setup()
    serve_version("0.76.0", build_id="aaaaaaa")
    modlog.bot.moderation_head = 50
    asyncio.run(modlog.check_moderation_cursor())
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    modlog.bot.moderation_head = 200

    async def run():
        await modlog.check_moderation_cursor()
        while modlog.bot._background_tasks:
            await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())
    assert moderation_gap_count() == 1 and restart_gap_count() == 0


def test_a_changed_capability_list_counts_as_a_changed_build():
    setup()
    ready_with_head(50)
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    serve_version("0.76.0", capabilities=("push", "polls"))
    modlog.bot.moderation_head = 200

    async def run():
        await modlog.check_moderation_cursor()
        while modlog.bot._background_tasks:
            await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())
    assert restart_gap_count() == 1


def test_an_unreadable_version_keeps_the_gap_plain():
    setup()
    ready_with_head(50)
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    modlog.bot.client.respond("GET", "/version", {})
    modlog.bot.moderation_head = 200

    async def run():
        await modlog.check_moderation_cursor()
        while modlog.bot._background_tasks:
            await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())
    assert moderation_gap_count() == 1
    assert restart_gap_count() == 0
    assert stored_build() == "0.76.0+push"


def test_a_head_at_or_below_the_last_seq_is_not_a_gap():
    setup()
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    ready_with_head(100)
    assert moderation_gap_count() == 0


def test_an_event_landing_after_the_hello_closes_the_gap():
    setup()
    serve_version("0.76.0")
    modlog.MODERATION_SETTLE_SECONDS = 0.2
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    modlog.bot.moderation_head = 200

    async def run():
        await modlog.check_moderation_cursor()
        await modlog.note_moderation_seq({"type": "role.changed", "seq": 200})
        while modlog.bot._background_tasks:
            await list(modlog.bot._background_tasks)[0]

    asyncio.run(run())
    assert moderation_gap_count() == 0


def test_a_server_without_a_moderation_head_changes_nothing():
    setup()
    ready_with_head(None)
    assert stored_moderation_seq() is None


def test_modlog_gaps_mentions_a_cursor_gap_with_no_reconnect_gap():
    client = setup()
    client.respond("GET", "/users/u1", {**MEMBERS[0]})
    dispatch_frame({"type": "member.removed", "user_id": "u1", "seq": 100})
    ready_in_one_loop((200, "0.76.0"), (300, "0.77.0"))
    process(client, message("u1", "!modlog gaps"))
    reply = client.sent[-1]["content"]
    assert "ahead of this log 1 time(s) on an unchanged server build" in reply
    assert "1 more time(s) it was ahead after the server restarted" in reply


def test_another_bot_is_ignored_by_default():
    client = setup()
    client.respond(
        "GET",
        "/members",
        MEMBERS + [{"id": "bot-2", "username": "otherbot", "display_name": "OtherBot", "is_bot": True, "is_webhook": False, "role_ids": [], "roles": []}],
    )
    asyncio.run(modlog.bot.space.refresh_members())
    process(client, message("bot-2", "!modlog stats"))
    assert client.sent == []


if __name__ == "__main__":
    run_module_tests(globals())
