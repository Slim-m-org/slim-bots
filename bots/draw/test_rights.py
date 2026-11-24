#!/usr/bin/env python3
"""Who may have the bot draw: the invoker's own canvas rights in their call, and the bot's; run directly: python3 test_rights.py."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import test_ai  # noqa: E402
import test_bot as base  # noqa: E402
from test_bot import draw  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

SPECTATOR = Permissions.VIEW_CHANNEL | Permissions.CONNECT
ANN_IN_CALL = "/members/u-ann/permissions?channel_id=c-voice"


def ann_in_call(bits):
    return {"user_id": "u-ann", "permissions": bits, "channel_id": "c-voice"}


def test_a_canvas_spectator_is_refused_before_any_model_run():
    planner = test_ai.FakePlanner()
    rig = test_ai.rig_with(planner)
    rig.client.respond("GET", ANN_IN_CALL, ann_in_call(SPECTATOR))
    reply = rig.say("!draw a cat", permissions=Permissions.USE_CANVAS)
    assert "you cannot draw" in reply and "lounge" in reply
    assert not planner.seen and not rig.client.placed


def test_a_canvas_spectator_cannot_paste_a_picture():
    rig = base.Rig()
    rig.client.respond("GET", ANN_IN_CALL, ann_in_call(SPECTATOR))
    assert "you cannot draw" in rig.say("!draw paste cat")
    assert not rig.client.placed and not rig.searches


def test_unreadable_invoker_rights_refuse_the_drawing():
    rig = base.Rig()
    rig.client.respond("GET", ANN_IN_CALL, ApiError(500, {"error": "boom"}))
    assert "you cannot draw" in rig.say("!draw cat")
    assert not rig.client.placed


def test_a_member_who_may_draw_in_the_call_still_gets_a_drawing():
    rig = base.Rig()
    rig.say("!draw cat")
    assert rig.client.placed


def test_clear_needs_manage_canvas_in_the_call_itself():
    rig = base.Rig()
    rig.client.respond("GET", ANN_IN_CALL, ann_in_call(Permissions.VIEW_CHANNEL | Permissions.USE_CANVAS))
    reply = rig.say("!draw clear", permissions=Permissions.MANAGE_CANVAS)
    assert "Manage Canvas" in reply and "removed" not in reply


def test_manage_canvas_granted_only_in_the_call_clears():
    rig = base.Rig()
    rig.client.respond("GET", ANN_IN_CALL, ann_in_call(Permissions.VIEW_CHANNEL | Permissions.MANAGE_CANVAS))
    assert "removed 0 drawings" in rig.say("!draw clear")


def test_a_channel_that_denies_the_bot_the_canvas_is_named_before_any_model_run():
    planner = test_ai.FakePlanner()
    rig = test_ai.rig_with(planner)
    rig.client.respond("GET", "/channels/c-voice/permissions", {"permissions": Permissions.VIEW_CHANNEL})
    assert "a setting on that channel" in rig.say("!draw a cat")
    assert not planner.seen and not rig.client.placed
    assert not any(path.endswith("/canvas/objects") and method == "GET" for method, path, *_ in rig.client.calls)


if __name__ == "__main__":
    run_module_tests(globals())
