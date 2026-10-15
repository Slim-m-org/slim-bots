#!/usr/bin/env python3
"""The alert lifecycle against FakeAsyncClient, no server needed; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("UPTIME_RELAY_SECRET", "test-secret")

import bot as relay_bot  # noqa: E402
from relay import Alert  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402
from slimbots.text import format_duration  # noqa: E402

CHANNEL = "c-alerts"
DOWN = Alert("kuma", "Jellyfin", True, "connect refused", "http://10.0.0.5:8096")
UP = Alert("kuma", "Jellyfin", False, "200 - OK")


def setup():
    client = FakeAsyncClient(me_id="bot-1")
    relay_bot.bot.client = client
    relay_bot.bot.channels = {CHANNEL}
    relay_bot.bot.store = None
    relay_bot.bot.data_path = os.path.join(tempfile.mkdtemp(), "uptime-relay.db")
    asyncio.run(relay_bot.bot.open_store(migrate=relay_bot.init_db))
    return client


def handle(alert, now):
    asyncio.run(relay_bot.handle(alert, now=now))


def allow_edits(client):
    message_id = client.sent[-1]["id"]
    client.respond("PATCH", f"/channels/{CHANNEL}/messages/{message_id}", {"id": message_id})
    return message_id


def edits(client):
    return [body["content"] for method, _, body, _ in client.calls if method == "PATCH"]


def press(custom_id):
    frame = {
        "type": "interaction.created", "interaction_id": "i1", "channel_id": CHANNEL, "message_id": "m",
        "custom_id": custom_id, "user_id": "u-1", "user_display_name": "Sam", "created_at": 1,
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

def test_durations_read_naturally():
    assert format_duration(30, "minutes") == "1 min"
    assert format_duration(25 * 60, "minutes") == "25 min"
    assert format_duration(2 * 3600, "minutes") == "2 h"
    assert format_duration(2 * 3600 + 5 * 60, "minutes") == "2 h 5 min"


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
