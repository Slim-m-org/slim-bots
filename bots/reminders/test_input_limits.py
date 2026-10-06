#!/usr/bin/env python3
"""Duration and time input the reminders bot refuses before any write; run directly: python3 test_input_limits.py."""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as reminders  # noqa: E402
import recurrence  # noqa: E402
from test_bot import message, process, setup  # noqa: E402

YEAR = 365 * 86400


def pending():
    return reminders.pending_for_user(reminders.bot.store.connection, "c1", "u1")


def test_a_duration_beyond_the_limit_is_refused_without_creating_one():
    for command in ("!remind in 9999999d x", "!remind in 99999999999999d x", "!remind every 9999999d x"):
        client = setup()
        process(client, message(command))
        assert len(client.sent) == 1 and "at most" in client.sent[-1]["content"], (command, client.sent)
        assert pending() == [], command


def test_a_year_is_still_allowed():
    client = setup()
    process(client, message("!remind in 365d x"))
    assert "will remind you" in client.sent[-1]["content"]


def test_snooze_beyond_the_limit_leaves_the_reminder_alone():
    client = setup()
    process(client, message("!remind in 1h x"))
    process(client, message("!reminders", "m2"))
    due = pending()[0][1]
    process(client, message("!reminders snooze 1 9999999d", "m3"))
    assert "at most" in client.sent[-1]["content"]
    assert pending()[0][1] == due


def test_the_listing_survives_a_row_with_an_unshowable_date():
    client = setup()
    reminders.add_reminder(reminders.bot.store.connection, "p", "c1", "u1", "m0", int(time.time()) + 9999999 * 86400, "x")
    process(client, message("!reminders"))
    assert len(client.sent) == 1 and "x" in client.sent[-1]["content"], client.sent


def test_a_unicode_digit_is_not_a_duration_or_a_time():
    client = setup()
    process(client, message("!remind in ²m x"))
    process(client, message("!remind at ²:00 x", "m2"))
    assert len(client.sent) == 2 and pending() == []


def test_format_local_never_raises_on_an_epoch_out_of_range():
    assert isinstance(recurrence.format_local(10**15, "UTC"), str)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
