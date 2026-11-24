#!/usr/bin/env python3
"""Attachment size, undo while placing, undo past a deleted object and free-space paging; run directly: python3 test_audit24.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan  # noqa: E402
import screenshot  # noqa: E402
import test_bot as base  # noqa: E402
from slimbots.canvas import Canvas  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402
from test_bot import draw  # noqa: E402
from test_photo import box_and_circle, say_with, shot  # noqa: E402

MIB = 1024 * 1024


def test_a_five_mib_attachment_is_accepted():
    rig = base.Rig()
    rig.client.respond("GET", "/attachments/a1", shot(box_and_circle) + b"\0" * (5 * MIB))
    say_with(rig, "!draw photo", attachments=[{"id": "a1", "content_type": "image/png"}])
    assert rig.client.placed


def test_an_over_limit_attachment_is_called_over_the_limit_not_unreadable():
    rig = base.Rig()
    rig.client.respond("GET", "/attachments/a1", b"\0" * (draw.MAX_ATTACHMENT_BYTES + 1))
    reply = say_with(rig, "!draw photo", attachments=[{"id": "a1", "content_type": "image/png"}])
    assert "over" in reply and "could not read" not in reply
    assert not rig.client.placed


def test_undo_and_clear_are_refused_while_the_call_is_still_being_drawn_in():
    rig = base.Rig()
    rig.say("!draw cat")
    draw.busy.add("c-voice")
    assert "try again" in rig.say("!draw undo")
    assert "try again" in rig.say("!draw clear", permissions=Permissions.MANAGE_CANVAS)
    assert not rig.client.ops
    draw.busy.clear()
    assert "removed your drawing" in rig.say("!draw undo")


class MissingClient(base.CanvasClient):
    missing = set()

    async def call(self, method, path, body=None, **kw):
        if path.endswith("/canvas/ops") and body.get("kind") == "remove" and self.missing & set(body["object_ids"]):
            raise ApiError(404, {"error": "not found"})
        return await super().call(method, path, body, **kw)


def rig_with_a_deleted_object():
    rig = base.Rig()
    rig.client.__class__ = MissingClient
    rig.say("!draw cat")
    ids = [p["id"] for p in rig.client.placed]
    rig.client.missing = {ids[0]}
    return rig, ids


def test_undo_succeeds_when_one_object_of_the_drawing_was_already_deleted():
    rig, ids = rig_with_a_deleted_object()
    assert "removed your drawing" in rig.say("!draw undo")
    removed = {i for op in rig.client.ops for i in op["object_ids"]}
    assert set(ids) - rig.client.missing <= removed and len(ids) > 1
    assert "no drawing to undo" in rig.say("!draw undo")


def test_clear_carries_on_past_a_drawing_that_cannot_be_removed():
    rig, ids = rig_with_a_deleted_object()
    rig.say("!draw dog", user=base.BOB)
    assert "removed 2 drawings" in rig.say("!draw clear", permissions=Permissions.MANAGE_CANVAS)


def test_clear_reports_a_drawing_that_failed_and_still_clears_the_rest():
    rig = base.Rig()
    rig.say("!draw cat")
    rig.say("!draw dog", user=base.BOB)
    first = [p["id"] for p in rig.client.placed][0]
    real = rig.client.call

    async def flaky(method, path, body=None, **kw):
        if path.endswith("/canvas/ops") and first in (body or {}).get("object_ids", []):
            raise ApiError(500, {"error": "boom"})
        return await real(method, path, body, **kw)

    rig.client.call = flaky
    reply = rig.say("!draw clear", permissions=Permissions.MANAGE_CANVAS)
    assert "removed 1 drawing" in reply and "1 could not" in reply


class PagedClient(base.CanvasClient):
    """The server's rule: no cursor gives the newest `limit` with has_more; cursor_seq walks forward from that seq."""

    def __init__(self, objects):
        super().__init__()
        self.all, self.requests = objects, []

    async def call(self, method, path, body=None, **kw):
        if path.endswith("/canvas/objects") and method == "GET":
            params = kw.get("params") or {}
            self.requests.append(dict(params))
            limit = params["limit"]
            if "cursor_seq" not in params:
                page = self.all[-limit:]
                return {"objects": page, "has_more": len(self.all) > limit, "latest_seq": self.all[-1]["seq"]}
            after = [o for o in self.all if o["seq"] > params["cursor_seq"]]
            page = after[:limit]
            more = len(after) > limit
            return {"objects": page, "has_more": more, "next_cursor": page[-1]["seq"] if more else None}
        return await super().call(method, path, body, **kw)


def crowd(count, at=(10, 10)):
    return [{"id": f"o{i}", "seq": i + 1, "kind": "stroke", "x": at[0], "y": at[1], "w": 20, "h": 20} for i in range(count)]


WINDOW = plan.Region(0, 0, 1600, 1000)


def read(objects):
    client = PagedClient(objects)
    canvas = Canvas(client, "c1")
    return asyncio.run(screenshot.read_objects(canvas, WINDOW)), client


def test_free_space_detection_sees_objects_beyond_the_newest_500():
    found, client = read(crowd(1200))
    assert len(found) == 1200
    assert [r["cursor_seq"] for r in client.requests if "cursor_seq" in r][0] == 0


def test_a_window_too_crowded_to_read_within_the_page_cap_is_refused():
    try:
        read(crowd(screenshot.PAGE * (screenshot.PAGES + 2)))
    except screenshot.ScreenshotError as err:
        assert "too crowded" in str(err)
    else:
        raise AssertionError("expected ScreenshotError")


if __name__ == "__main__":
    run_module_tests(globals())
