#!/usr/bin/env python3
"""The day and time parser with a pinned clock; run directly: python3 test_planning.py."""

import sys
import os
from datetime import datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import planning  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

# A Wednesday afternoon.
NOW = datetime(2026, 11, 4, 15, 0, tzinfo=ZoneInfo("Europe/Berlin"))


def start_of(text):
    return planning.parse_event(text, NOW)[1]


def fails(text):
    try:
        planning.parse_event(text, NOW)
    except ValueError as err:
        return str(err)
    raise AssertionError(f"{text!r} was accepted")


def test_a_quoted_title_weekday_and_pm_time():
    title, start = planning.parse_event('"movie night" fri 8pm', NOW)
    assert title == "movie night" and start == datetime(2026, 11, 6, 20, 0, tzinfo=NOW.tzinfo)


def test_an_unquoted_title_works_too():
    assert planning.parse_event("game night sat 19:30", NOW)[0] == "game night"
    assert start_of("game night sat 19:30").minute == 30


def test_today_later_is_today_and_a_passed_weekday_time_rolls_a_week():
    assert start_of("x today 6pm").day == 4
    assert start_of("x wed 9am") == datetime(2026, 11, 11, 9, 0, tzinfo=NOW.tzinfo)


def test_a_date_and_tomorrow():
    assert start_of("x 2026-12-01 8:15pm") == datetime(2026, 12, 1, 20, 15, tzinfo=NOW.tzinfo)
    assert start_of("x tomorrow 20:00").day == 5


def test_noon_and_midnight_edges():
    assert start_of("x fri 12pm").hour == 12 and start_of("x fri 12am").hour == 0


def test_bad_input_gets_a_sentence():
    assert "title, a day and a time" in fails("fri")
    assert "could not read" in fails("x someday 8pm")
    assert "could not read" in fails("x fri 25:00")
    assert "already passed" in fails("x today 9am")
    assert "within 60 days" in fails("x 2027-06-01 8pm")


def test_an_at_sign_in_the_title_is_defused():
    assert "@" not in planning.parse_event("@everyone fri 8pm", NOW)[0]


if __name__ == "__main__":
    run_module_tests(globals())
