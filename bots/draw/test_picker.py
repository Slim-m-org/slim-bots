#!/usr/bin/env python3
"""What the composer is told about `!draw`: one command whose first word offers the modes without refusing a free-text idea; run directly: python3 test_picker.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_ai as ai_tests  # noqa: E402
import test_bot as base  # noqa: E402
from test_bot import draw  # noqa: E402
from slimbots.registration import registration_body  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402


def entry():
    body = registration_body("!", draw.bot.unique_commands())
    return next(c for c in body["commands"] if c["name"] == "draw")


def test_draw_is_registered_with_a_pick_list_of_its_modes():
    assert entry()["options"] == [{"name": "mode", "required": False, "choices": list(draw.MODES)}]
    assert draw.MODES == ("photo", "shaded", "color", "paste", "trace", "undo", "clear")


def test_the_registered_description_names_every_mode_within_the_servers_limit():
    description = entry()["description"]
    assert 0 < len(description) <= 100 and len(entry().get("usage", "")) <= 80
    for mode in draw.MODES:
        assert mode in description


def test_every_mode_in_the_list_is_a_real_subcommand_and_the_old_dots_name_still_works():
    names = {name for sub in draw.draw.unique_subcommands() for name in sub.names}
    assert set(draw.MODES) <= names and "dots" in names


def test_a_free_text_idea_is_not_refused_just_because_the_modes_are_suggested():
    planner = ai_tests.FakePlanner()
    rig = ai_tests.rig_with(planner)
    rig.say("!draw michael jordan dunking")
    assert planner.seen and planner.seen[0][0] == "michael jordan dunking" and rig.client.placed


def test_a_mode_picked_from_the_list_arrives_with_a_trailing_space_and_still_runs():
    rig = base.Rig()
    rig.say("!draw trace cat")
    assert "A cat" in rig.client.sent[-1]["content"]
    base.Rig().say("!draw undo ")
    rig = base.Rig()
    assert "no drawing to undo" in rig.say("!draw undo ")


def test_a_mode_word_still_reaches_its_command_and_the_bare_command_explains_every_mode():
    rig = base.Rig()
    reply = rig.say("!draw")
    for mode in ("photo", "shaded", "color", "paste", "trace", "undo", "clear"):
        assert f"draw {mode}" in reply
    assert "AI drawing" in reply


if __name__ == "__main__":
    run_module_tests(globals())

