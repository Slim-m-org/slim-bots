#!/usr/bin/env python3
"""Commands, buttons and the reminder loop against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as events  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.testing import FakeAsyncClient, human, run_module_tests, wire_bot  # noqa: E402

ANN = human("u-ann", "ann", "Ann")
BOB = human("u-bob", "bob", "Bob")


class EditingClient(FakeAsyncClient):
    """Answers a message edit, which the plain fake has no default for."""

    async def call(self, method, path, body=None, **kw):
        if method == "PATCH" and "/messages/" in path:
            self.calls.append((method, path, body, None))
            return {}
        return await super().call(method, path, body, **kw)


def setup():
    events.TZ_NAME, events.REMIND_MINUTES = "UTC", 15
    client = EditingClient(me_id="bot-1")
    for person in (ANN, BOB):
        client.respond("GET", f"/users/{person['id']}", {**person, "role_ids": []})
    wire_bot(events.bot, client, db_name="events.db")
    asyncio.run(events.bot.open_store(migrate=events.init_db))
    return client


def say(text, user=ANN, permissions=0):
    member = events.bot.space._make_member({**user, "role_ids": []})
    member._base_permissions = permissions
    events.bot.space.members[user["id"]] = member
    asyncio.run(events.bot.process_message({"id": "m-cmd", "channel_id": "c1", "author_id": user["id"], "content": text, "seq": 1}))


def press(custom_id, user_id):
    frame = {"type": "interaction.created", "interaction_id": "i1", "channel_id": "c1", "message_id": "m-event", "custom_id": custom_id, "user_id": user_id, "user_display_name": "x", "created_at": 1}

    async def run():
        await events.bot._handle_frame(frame)
        await asyncio.gather(*list(events.bot._background_tasks))

    asyncio.run(run())


def plan(client):
    say('!event "movie night" fri 8pm')
    return client.sent[-1]


def test_an_event_posts_two_buttons_and_the_time():
    client = setup()
    sent = plan(client)
    assert "movie night" in sent["content"] and "0 going" in sent["content"]
    assert [b["custom_id"] for b in sent["components"][0]["buttons"]] == ["event:1:yes", "event:1:no"]


def test_a_bad_event_is_explained_privately():
    client = setup()
    say("!event soon")
    assert "title, a day and a time" in client.ephemerals[-1]["content"]
    assert not client.sent


def test_pressing_going_counts_once_and_not_going_takes_it_back():
    client = setup()
    plan(client)
    press("event:1:yes", "u-bob")
    press("event:1:yes", "u-bob")
    edit = [c for c in client.calls if c[0] == "PATCH"][-1]
    assert "1 going" in edit[2]["content"]
    press("event:1:no", "u-bob")
    assert "0 going" in [c for c in client.calls if c[0] == "PATCH"][-1][2]["content"]


def move_start(minutes_from_now):
    asyncio.run(events.bot.store.run(lambda c: c.execute("UPDATE events SET start_ts = ?", (int(time.time()) + minutes_from_now * 60,)) and c.commit()))


def test_the_people_going_are_reminded_once_before_and_once_at_the_start():
    client = setup()
    plan(client)
    press("event:1:yes", "u-bob")
    press("event:1:no", "u-ann")
    move_start(10)
    asyncio.run(events.tick())
    asyncio.run(events.tick())
    reminders = [s for s in client.sent if "starts at" in s["content"]]
    assert len(reminders) == 1 and "@bob" in reminders[0]["content"] and "@ann" not in reminders[0]["content"]
    move_start(-1)
    asyncio.run(events.tick())
    asyncio.run(events.tick())
    assert len([s for s in client.sent if "starting now" in s["content"]]) == 1


def test_nobody_going_means_no_post():
    client = setup()
    plan(client)
    move_start(5)
    before = len(client.sent)
    asyncio.run(events.tick())
    assert len(client.sent) == before


def test_list_and_cancel_with_the_right_permission():
    client = setup()
    plan(client)
    say("!event list", user=BOB)
    assert "#1 movie night" in client.ephemerals[-1]["content"]
    say("!event cancel 1", user=BOB)
    assert "only the person who planned it" in client.ephemerals[-1]["content"]
    say("!event cancel 1", user=BOB, permissions=Permissions.MANAGE_MESSAGES)
    assert "cancelled #1" in client.ephemerals[-1]["content"]
    say("!event list")
    assert "no events" in client.ephemerals[-1]["content"]
    press("event:1:yes", "u-bob")
    assert "over" in client.ephemerals[-1]["content"]


def test_the_open_event_cap_holds():
    client = setup()
    for _ in range(events.MAX_OPEN_EVENTS):
        plan(client)
    say('!event "one more" fri 9pm')
    assert "already" in client.ephemerals[-1]["content"]


if __name__ == "__main__":
    run_module_tests(globals())
