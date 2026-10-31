#!/usr/bin/env python3
"""Commands, the op budget and undo against FakeAsyncClient and scripted sources; run directly: python3 test_bot.py."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as draw  # noqa: E402
import sources  # noqa: E402
from slimbots import Canvas  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.testing import FakeAsyncClient, human, run_module_tests, wire_bot  # noqa: E402

ANN = human("u-ann", "ann", "Ann")
BOB = human("u-bob", "bob", "Bob")
CANDIDATE = {"url": "https://t/1.jpg", "title": "A cat", "creator": "Sam", "license": "CC BY 2.0", "landing": "https://page/1"}


def disc():
    return [[0 if (x - 24) ** 2 + (y - 24) ** 2 <= 196 else 255 for x in range(48)] for y in range(48)]


class CanvasClient(FakeAsyncClient):
    def __init__(self):
        super().__init__(me_id="bot-1")
        self.placed, self.ops, self.rate_limit_next = [], [], 0

    async def call(self, method, path, body=None, **kw):
        if path.endswith("/canvas/objects") and method == "POST":
            if self.rate_limit_next:
                self.rate_limit_next -= 1
                raise ApiError(429, {"error": "rate limited"})
            self.placed.append(body)
            return {**body, "seq": len(self.placed)}
        if path.endswith("/canvas/ops"):
            self.ops.append(body)
            return {"affected": len(body.get("object_ids", []))}
        return await super().call(method, path, body, **kw)


class Rig:
    def __init__(self, grid=None, results=None):
        draw.PACE_SECONDS, draw.COOLDOWN_SECONDS, draw.MAX_STROKES, draw.RATE_BACKOFF = 0, 0, 150, 0
        draw.last_draw.clear()
        self.client = CanvasClient()
        self.client.respond("GET", "/users/u-ann", {**ANN, "role_ids": []})
        self.client.respond("GET", "/users/u-bob", {**BOB, "role_ids": []})
        wire_bot(draw.bot, self.client, db_name="draw.db")
        asyncio.run(draw.bot.open_store(migrate=draw.init_db))
        draw.canvas = Canvas(self.client, "c-voice")
        self.searches, self.grid = [], grid if grid is not None else disc()

        def search(get, query, limit=8):
            self.searches.append(query)
            return [CANDIDATE] if results is None else results

        sources.search = search
        sources.fetch_picture = lambda get, url: b"bytes"
        sources.decode_gray = lambda data, size: (self.grid, len(self.grid[0]), len(self.grid))

        async def no_gateway(frame):
            return None

        draw.bot.send_frame = no_gateway

    def say(self, text, user=ANN, permissions=0):
        member = draw.bot.space._make_member({**user, "role_ids": []})
        member._base_permissions = permissions
        draw.bot.space.members[user["id"]] = member
        asyncio.run(draw.bot.process_message({"id": "m1", "channel_id": "c1", "author_id": user["id"], "content": text, "seq": 1}))
        return self.client.ephemerals[-1]["content"] if self.client.ephemerals else ""


def test_draw_places_stroke_objects_inside_its_slot_and_credits_the_source():
    rig = Rig()
    rig.say("!draw cat")
    assert rig.client.placed and len(rig.client.placed) <= draw.MAX_STROKES
    assert all(p["kind"] == "stroke" for p in rig.client.placed)
    left, top = draw.corner(0)
    for p in rig.client.placed:
        assert left <= p["x"] and p["x"] + p["w"] <= left + draw.SIZE + 1 and top <= p["y"] and p["y"] + p["h"] <= top + draw.SIZE + 1
        assert len(json.dumps(p["props"])) < 4096 and len(p["props"]["points"]) % 2 == 0
    reply = rig.client.sent[-1]["content"]
    assert "A cat" in reply and "Sam" in reply and "CC BY 2.0" in reply and "https://page/1" in reply


def test_the_drawing_is_recognisably_the_picture_not_a_scribble():
    rig = Rig()
    rig.say("!draw cat")
    xs = [p["x"] for p in rig.client.placed] + [p["x"] + p["w"] for p in rig.client.placed]
    left, _ = draw.corner(0)
    scale = draw.SIZE / 48
    assert abs(min(xs) - left - 8.5 * scale) < 2 * scale and abs(max(xs) - left - 39.5 * scale) < 2 * scale


def test_a_busy_picture_stays_inside_the_op_budget():
    state, rows = 7, []
    for _ in range(96):
        row = []
        for _ in range(96):
            state = (state * 1103515245 + 12345) % 2**31
            row.append(state % 256)
        rows.append(row)
    rig = Rig(grid=rows)
    draw.MAX_STROKES = 40
    rig.say("!draw static")
    assert 0 < len(rig.client.placed) <= 40


def test_undo_removes_the_whole_drawing_in_batches_and_only_your_own():
    rig = Rig()
    rig.say("!draw cat")
    placed = [p["id"] for p in rig.client.placed]
    assert "no drawing to undo" in rig.say("!draw undo", user=BOB)
    assert "removed your drawing" in rig.say("!draw undo")
    removed = [i for op in rig.client.ops for i in op["object_ids"]]
    assert removed == placed and all(len(op["object_ids"]) <= 64 for op in rig.client.ops)
    assert "no drawing to undo" in rig.say("!draw undo")


def test_clear_needs_manage_canvas_and_removes_every_drawing():
    rig = Rig()
    rig.say("!draw cat")
    rig.say("!draw dog", user=BOB)
    assert "Manage Canvas" in rig.say("!draw clear")
    assert "removed 2 drawings" in rig.say("!draw clear", permissions=Permissions.MANAGE_CANVAS)
    assert len([i for op in rig.client.ops for i in op["object_ids"]]) == len(rig.client.placed)


def test_the_area_holds_six_drawings_and_undo_frees_a_slot():
    rig = Rig()
    for _ in range(draw.SLOTS):
        rig.say("!draw cat")
    rig.say("!draw cat")
    assert "holds 6 drawings" in rig.client.ephemerals[-1]["content"]
    rig.say("!draw undo")
    count = len(rig.client.placed)
    rig.say("!draw cat")
    assert len(rig.client.placed) > count


def test_a_rate_limited_placement_is_retried_not_dropped():
    rig = Rig()
    rig.client.rate_limit_next = 2
    rig.say("!draw cat")
    assert rig.client.placed and "strokes" in rig.client.sent[-1]["content"]


def test_nothing_found_a_flat_picture_and_a_missing_canvas_each_get_a_sentence():
    assert "no openly licensed picture" in Rig(results=[]).say("!draw zzzz")
    assert "could not turn" in Rig(grid=[[200] * 30 for _ in range(30)]).say("!draw blank")
    rig = Rig()
    draw.canvas = None
    assert "DRAW_CHANNEL" in rig.say("!draw cat")


def test_bad_input_and_the_per_member_cooldown():
    rig = Rig()
    assert "draw undo" in rig.say("!draw")
    assert "under 60 characters" in rig.say("!draw " + "x" * 80)
    draw.COOLDOWN_SECONDS = 60
    rig.say("!draw cat")
    assert "can draw again" in rig.say("!draw cat")
    assert not rig.searches[1:]


def test_pillow_missing_is_reported_once_not_tried_on_every_candidate():
    rig = Rig(results=[CANDIDATE, CANDIDATE, CANDIDATE])

    def no_pillow(data, size):
        raise sources.SourceError("Pillow is not installed on this bot, so it cannot read pictures")

    sources.decode_gray = no_pillow
    assert "Pillow is not installed" in rig.say("!draw cat")


def test_an_at_sign_in_the_query_cannot_ping():
    rig = Rig()
    rig.say("!draw @everyone")
    assert "@everyone" not in rig.client.sent[-1]["content"]


if __name__ == "__main__":
    run_module_tests(globals())
