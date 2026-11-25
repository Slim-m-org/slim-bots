#!/usr/bin/env python3
"""A call whose canvas is off: the invoker gets the off reply, not a 403; run directly: python3 test_canvas_off.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_bot as base  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.models import Channel  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

OFF = "The canvas is off in this channel; someone who manages channels can turn it on in its settings."


def rig_with(**flags):
    rig = base.Rig()
    base.draw.bot.space.channels["c-voice"] = Channel({"id": "c-voice", "name": "lounge", "kind": "voice", **flags})
    return rig


def test_a_call_with_the_canvas_off_gets_the_off_reply_for_every_command():
    rig = rig_with(canvas_enabled=False, voice_enabled=True)
    for command in ("!draw cat", "!draw photo cat", "!draw paste cat", "!draw undo", "!draw clear"):
        assert rig.say(command) == OFF, command
    assert rig.client.placed == [] and rig.client.batches == []


def test_the_canvas_flag_on_and_the_flags_absent_both_draw_as_before():
    for flags in ({"canvas_enabled": True, "voice_enabled": True}, {}):
        rig = rig_with(**flags)
        rig.say("!draw cat")
        assert rig.client.placed, flags


def test_a_canvas_channel_that_hosts_a_call_is_drawn_on():
    rig = base.Rig()
    base.draw.bot.space.channels["c-voice"] = Channel({"id": "c-voice", "name": "design", "kind": "canvas", "canvas_enabled": True, "voice_enabled": True})
    rig.say("!draw cat")
    assert rig.client.placed


def test_a_canvas_off_403_that_races_a_toggle_gets_the_same_reply():
    race = ApiError(403, {"error": "the canvas is off", "code": "canvas_off"})
    rig = rig_with(canvas_enabled=True)
    rig.client.viewport_error = race
    assert rig.say("!draw photo cat") == OFF
    rig = rig_with()
    rig.client.batch_error = race
    assert rig.say("!draw cat") == OFF


def test_other_forbidden_answers_keep_their_old_handling():
    rig = rig_with()
    rig.client.viewport_error = ApiError(403, {"error": "forbidden"})
    assert OFF not in rig.say("!draw photo cat")


if __name__ == "__main__":
    run_module_tests(globals())
