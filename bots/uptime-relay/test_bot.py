#!/usr/bin/env python3
"""The alert lifecycle against FakeAsyncClient, no server needed; run directly: python3 test_bot.py."""

import asyncio
import os
import tempfile

os.environ.setdefault("UPTIME_RELAY_SECRET", "test-secret")

import bot as relay_bot
import relay
from relay import Alert
from slimbots.http import ApiError
from slimbots.permissions import Permissions
from slimbots.testing import FakeAsyncClient, run_module_tests, wire_bot
from slimbots.text import format_duration

CHANNEL = "c-alerts"
DOWN = Alert("kuma", "Jellyfin", True, "connect refused", "http://10.0.0.5:8096")
UP = Alert("kuma", "Jellyfin", False, "200 - OK")
OPS_ROLE = {"id": "r-ops", "name": "Ops", "permissions": 0}
PEOPLE = {
    "u-1": {"id": "u-1", "username": "sam", "display_name": "Sam", "role_ids": ["r-ops"]},
    "u-2": {"id": "u-2", "username": "ana", "display_name": "Ana", "role_ids": []},
}


def setup():
    client = FakeAsyncClient(me_id="bot-1")
    client.respond("GET", "/roles", [OPS_ROLE])
    relay_bot.OPERATOR_ROLE = "Ops"
    wire_bot(relay_bot.bot, client, db_name="uptime-relay.db")
    relay_bot.bot.channels = {CHANNEL}
    asyncio.run(relay_bot.bot.space.refresh_roles())
    for person in PEOPLE.values():
        relay_bot.bot.space.members[person["id"]] = relay_bot.bot.space._make_member(person)
    asyncio.run(relay_bot.bot.open_store(migrate=relay_bot.init_db))
    client.calls.clear()
    return client


def handle(alert, now):
    asyncio.run(relay_bot.handle(alert, now=now))


def allow_edits(client):
    message_id = client.sent[-1]["id"]
    client.respond("PATCH", f"/channels/{CHANNEL}/messages/{message_id}", {"id": message_id})
    return message_id


def edits(client):
    return [body["content"] for method, _, body, _ in client.calls if method == "PATCH"]


def press(custom_id, user_id="u-1"):
    frame = {
        "type": "interaction.created", "interaction_id": "i1", "channel_id": CHANNEL, "message_id": "m",
        "custom_id": custom_id, "user_id": user_id, "user_display_name": PEOPLE[user_id]["display_name"], "created_at": 1,
    }

    async def run():
        await relay_bot.bot._handle_frame(frame)
        await asyncio.gather(*list(relay_bot.bot._background_tasks))

    asyncio.run(run())


def button_ids(components):
    return [b["custom_id"] for row in components for b in row["buttons"]]


def test_a_down_alert_posts_once_with_both_buttons():
    client = setup()
    handle(DOWN, now=1_000_000)
    [sent] = client.sent
    assert sent["content"].startswith("DOWN - Jellyfin (kuma)")
    assert "connect refused" in sent["content"] and "http://10.0.0.5:8096" in sent["content"]
    assert [b.split(":")[1] for b in button_ids(sent["components"])] == ["ack", "silence"]


def test_a_repeat_updates_the_post_instead_of_posting_again():
    client = setup()
    handle(DOWN, now=1_000_000)
    allow_edits(client)
    handle(DOWN, now=1_000_060)
    assert len(client.sent) == 1
    assert "2 alerts" in edits(client)[-1]


def test_recovery_resolves_the_post_and_clears_its_buttons():
    client = setup()
    handle(DOWN, now=1_000_000)
    message_id = allow_edits(client)
    handle(UP, now=1_000_000 + 25 * 60)
    assert edits(client)[-1] == "RESOLVED - Jellyfin (kuma) was down for 25 min."
    assert client.component_edits[-1] == {"channel_id": CHANNEL, "message_id": message_id, "components": []}
    handle(DOWN, now=1_100_000)
    assert len(client.sent) == 2, "a new outage after a recovery is a new alert"


def test_an_up_with_nothing_open_is_quiet():
    client = setup()
    handle(UP, now=1_000_000)
    assert not client.calls


def test_acknowledge_records_who_and_drops_its_button():
    client = setup()
    handle(DOWN, now=1_000_000)
    allow_edits(client)
    [ack_id, _] = button_ids(client.sent[-1]["components"])
    press(ack_id)
    assert "acknowledged by Sam" in edits(client)[-1]
    assert [b.split(":")[1] for b in button_ids(client.component_edits[-1]["components"])] == ["silence"]


def test_silence_suppresses_a_new_outage_until_it_expires():
    client = setup()
    handle(DOWN, now=int(relay_bot.time.time()))
    allow_edits(client)
    [_, silence_id] = button_ids(client.sent[-1]["components"])
    press(silence_id)
    assert "silenced by Sam until" in edits(client)[-1]
    now = int(relay_bot.time.time())
    handle(UP, now=now)
    handle(DOWN, now=now + 60)
    assert len(client.sent) == 1, "silenced: no new post"
    handle(DOWN, now=now + relay_bot.SILENCE_SECONDS + 60)
    assert len(client.sent) == 2, "the silence expired"


def test_a_press_on_a_resolved_alert_says_so():
    client = setup()
    press("ur:ack:999")
    assert "already resolved" in client.ephemerals[-1]["content"]


def test_two_webhooks_at_once_open_one_alert_and_count_both():
    client = setup()

    async def both():
        await asyncio.gather(relay_bot.handle_all([DOWN]), relay_bot.handle_all([DOWN]))

    real_call = client.call

    async def call(method, path, body=None, **kw):
        if method == "PATCH":
            client.respond(method, path, {"id": path.rsplit("/", 1)[1]})
        return await real_call(method, path, body, **kw)

    client.call = call
    asyncio.run(both())
    assert len(client.sent) == 1
    assert "2 alerts" in edits(client)[-1]

def delete_post(client, message_id):
    client.respond("PATCH", f"/channels/{CHANNEL}/messages/{message_id}", ApiError(404, {"error": "message not found"}))


def stored_message_id(key):
    return asyncio.run(relay_bot.bot.store.run(relay_bot.open_alert, key))["message_id"]


def test_a_repeat_after_the_post_was_deleted_posts_a_fresh_alert():
    client = setup()
    handle(DOWN, now=1_000_000)
    delete_post(client, client.sent[-1]["id"])
    handle(DOWN, now=1_000_060)
    assert len(client.sent) == 2
    assert "2 alerts" in client.sent[-1]["content"]
    assert [b.split(":")[1] for b in button_ids(client.sent[-1]["components"])] == ["ack", "silence"]
    assert stored_message_id(DOWN.key) == client.sent[-1]["id"]
    allow_edits(client)
    handle(DOWN, now=1_000_120)
    assert len(client.sent) == 2 and "3 alerts" in edits(client)[-1], "later repeats edit the new post"


def test_a_silenced_alert_whose_post_was_deleted_stays_quiet():
    client = setup()
    handle(DOWN, now=int(relay_bot.time.time()))
    message_id = allow_edits(client)
    [_, silence_id] = button_ids(client.sent[-1]["components"])
    press(silence_id)
    delete_post(client, message_id)
    handle(DOWN, now=int(relay_bot.time.time()) + 60)
    assert len(client.sent) == 1


def test_recovery_after_the_post_was_deleted_announces_it_and_closes_the_alert():
    client = setup()
    handle(DOWN, now=1_000_000)
    delete_post(client, client.sent[-1]["id"])
    handle(UP, now=1_000_000 + 25 * 60)
    assert client.sent[-1]["content"] == "RESOLVED - Jellyfin (kuma) was down for 25 min."
    handle(DOWN, now=1_100_000)
    assert client.sent[-1]["content"].startswith("DOWN - Jellyfin"), "the alert is closed, so a new outage opens a new one"


def test_recovery_closes_the_alert_even_when_clearing_the_buttons_is_refused():
    client = setup()
    handle(DOWN, now=1_000_000)
    allow_edits(client)
    real_call = client.call

    async def call(method, path, body=None, **kw):
        if method == "PUT" and path.endswith("/components"):
            raise ApiError(404, {"error": "message not found"})
        return await real_call(method, path, body, **kw)

    client.call = call
    handle(UP, now=1_000_000 + 60)
    handle(DOWN, now=1_100_000)
    assert len(client.sent) == 2


def test_a_name_or_message_cannot_ping():
    client = setup()
    handle(Alert("generic", "@everyone", True, "call @[Core Team] now"), now=1_000_000)
    content = client.sent[-1]["content"]
    assert "\\@everyone" in content and "\\@[Core Team]" in content
    assert content.count("@") == content.count("\\@")


def test_durations_read_naturally():
    assert format_duration(30, "minutes") == "1 min"
    assert format_duration(25 * 60, "minutes") == "25 min"
    assert format_duration(2 * 3600, "minutes") == "2 h"
    assert format_duration(2 * 3600 + 5 * 60, "minutes") == "2 h 5 min"


UNREACHABLE = ApiError(None, "connection refused")
MESSAGES = f"/channels/{CHANNEL}/messages"


def deliver(alerts):
    return asyncio.run(relay_bot.handle_all(alerts))


def retry():
    return asyncio.run(relay_bot.retry_pending())


def test_a_webhook_during_an_outage_is_refused_then_posted_once_slim_m_returns():
    client = setup()
    client.respond("POST", MESSAGES, UNREACHABLE)
    assert deliver([DOWN]) is False, "the listener answers 503 for this"
    first_id = client.sent[-1]["id"]
    retry()
    del client._responses[("POST", MESSAGES)]
    retry()
    posted = [s for s in client.sent if s["content"].startswith("DOWN - Jellyfin")]
    assert len(posted) == 3 and {s["id"] for s in posted} == {first_id}, "every attempt reuses one message id, so it posts once"
    allow_edits(client)
    retry()
    assert len(client.sent) == 3, "a delivered alert is not retried"
    assert deliver([UP]) is True
    assert edits(client)[-1].startswith("RESOLVED - Jellyfin")


def test_an_alert_held_for_a_service_keeps_later_alerts_for_it_in_order():
    client = setup()
    client.respond("POST", MESSAGES, UNREACHABLE)
    assert deliver([DOWN]) is False
    assert deliver([UP]) is False, "the recovery waits behind the outage it ends"
    del client._responses[("POST", MESSAGES)]
    allow_edits(client)
    retry()
    assert edits(client)[-1].startswith("RESOLVED - Jellyfin")
    assert stored(DOWN.key) is None


def test_a_recovery_whose_edit_fails_keeps_the_alert_open_and_retries_it():
    client = setup()
    deliver([DOWN])
    message_id = client.sent[-1]["id"]
    client.respond("PATCH", f"{MESSAGES}/{message_id}", ApiError(502, "bad gateway"))
    assert deliver([UP]) is False
    assert stored(DOWN.key) is not None, "the post still says DOWN, so the alert stays open"
    allow_edits(client)
    retry()
    assert edits(client)[-1].startswith("RESOLVED - Jellyfin")
    assert client.component_edits[-1]["components"] == []
    assert stored(DOWN.key) is None


def test_held_alerts_survive_a_restart():
    client = setup()
    client.respond("POST", MESSAGES, UNREACHABLE)
    deliver([DOWN])
    asyncio.run(relay_bot.bot.store.close())
    relay_bot.bot.store = None
    asyncio.run(relay_bot.bot.open_store(migrate=relay_bot.init_db))
    del client._responses[("POST", MESSAGES)]
    retry()
    assert stored(DOWN.key)["posted"]


def test_an_alert_held_past_a_day_is_given_up_and_later_ones_go_through():
    client = setup()
    client.respond("POST", MESSAGES, UNREACHABLE)
    deliver([DOWN])
    asyncio.run(relay_bot.bot.store.run(lambda conn: (conn.execute("UPDATE pending SET at = at - ?", (relay_bot.HOLD_SECONDS + 1,)), conn.commit())))
    del client._responses[("POST", MESSAGES)]
    retry()
    assert stored(DOWN.key) is None, "the stale outage is not posted"
    assert asyncio.run(relay_bot.bot.store.run(relay_bot.held_alerts)) == []
    sent = len(client.sent)
    assert deliver([DOWN]) is True
    assert len(client.sent) == sent + 1


def test_a_refused_alert_holds_only_its_own_service():
    client = setup()
    client.respond("POST", MESSAGES, ApiError(403, {"error": "missing permission"}))
    other = Alert("kuma", "Sonarr", True, "timeout")
    assert deliver([DOWN]) is False
    del client._responses[("POST", MESSAGES)]
    real_call = client.call

    async def call(method, path, body=None, **kw):
        if method == "POST" and "Jellyfin" in (body or {}).get("content", ""):
            raise ApiError(403, {"error": "missing permission"})
        return await real_call(method, path, body, **kw)

    client.call = call
    assert deliver([other]) is True, "another service is not stuck behind it"
    assert stored(other.key)["posted"] and not stored(DOWN.key)["posted"]


def stored(key):
    return asyncio.run(relay_bot.bot.store.run(relay_bot.open_alert, key))


def _host_down(status, instance):
    labels = {"alertname": "HostDown", "instance": instance}
    item = {"status": status, "labels": labels, "fingerprint": f"fp-{instance}", "annotations": {"summary": f"{instance} unreachable"}}
    return relay.parse("grafana", {"alerts": [item]})[0]


def test_two_grafana_instances_of_one_rule_resolve_independently():
    client = setup()
    deliver([_host_down("firing", "a"), _host_down("firing", "b")])
    assert [s["content"].splitlines()[0] for s in client.sent] == ["DOWN - HostDown on a (grafana)", "DOWN - HostDown on b (grafana)"]
    for sent in client.sent:
        client.respond("PATCH", f"{MESSAGES}/{sent['id']}", {"id": sent["id"]})
    deliver([_host_down("resolved", "a")])
    assert [text.split(" was down")[0] for text in edits(client)] == ["RESOLVED - HostDown on a (grafana)"]
    assert stored(_host_down("firing", "b").key) is not None, "b is still down"
    assert stored(_host_down("firing", "a").key) is None


def test_a_grafana_alert_opened_before_the_upgrade_still_resolves():
    client = setup()
    legacy = Alert("grafana", "HostDown", True, "a unreachable")
    handle(legacy, now=1_000_000)
    allow_edits(client)
    deliver([_host_down("resolved", "a")])
    assert edits(client)[-1].startswith("RESOLVED - HostDown (grafana)")
    assert stored(legacy.key) is None


def test_a_member_without_the_operator_role_cannot_acknowledge_or_silence():
    client = setup()
    handle(DOWN, now=1_000_000)
    allow_edits(client)
    for custom_id in button_ids(client.sent[-1]["components"]):
        press(custom_id, user_id="u-2")
        assert "operator" in client.ephemerals[-1]["content"]
    assert not edits(client) and not client.component_edits
    row = stored(DOWN.key)
    assert row["acked_by"] is None
    assert asyncio.run(relay_bot.bot.store.run(relay_bot.silence_of, DOWN.key, 1_000_000)) is None


def test_a_member_with_manage_server_may_acknowledge_without_the_role():
    client = setup()
    relay_bot.bot.space.members["u-2"]._base_permissions = Permissions.MANAGE_SERVER
    handle(DOWN, now=1_000_000)
    allow_edits(client)
    [ack_id, _] = button_ids(client.sent[-1]["components"])
    press(ack_id, user_id="u-2")
    assert "acknowledged by Ana" in edits(client)[-1]


def test_with_no_operator_role_configured_only_manage_server_may_press():
    client = setup()
    relay_bot.OPERATOR_ROLE = ""
    handle(DOWN, now=1_000_000)
    allow_edits(client)
    [ack_id, _] = button_ids(client.sent[-1]["components"])
    press(ack_id)
    assert "operator" in client.ephemerals[-1]["content"]


if __name__ == "__main__":
    run_module_tests(globals())
