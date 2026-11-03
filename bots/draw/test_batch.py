#!/usr/bin/env python3
"""Batch placement, the fallback to single places, undo groups and parallel removal; run directly: python3 test_batch.py."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan  # noqa: E402
import test_bot as base  # noqa: E402
from test_bot import ANN, draw  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402


def objects(count):
    return [plan.Planned("stroke", float(i), 0.0, 5.0, 5.0, {"points": [0, 0, 5, 5], "width": 2.0, "color": "annotation"}) for i in range(count)]


def place(rig, count):
    async def run():
        started = await draw.bot.store.run(draw.new_drawing, "u-ann", "x", "c-voice", False)
        return started[0], await draw.place_all(draw.bot.canvas("c-voice"), started[0], objects(count))

    return asyncio.run(run())


def stored_ids(drawing_id):
    return asyncio.run(draw.bot.store.run(draw.stroke_ids, drawing_id))


def batching_rig():
    rig = base.Rig()
    rig.client.batch_error = None
    return rig


def test_a_drawing_goes_down_in_batches_of_at_most_200_with_every_id_kept():
    rig = batching_rig()
    drawing_id, done = place(rig, 450)
    assert done == 450 and [len(b["objects"]) for b in rig.client.batches] == [200, 200, 50]
    assert len(stored_ids(drawing_id)) == 450 and len(set(stored_ids(drawing_id))) == 450
    assert [o["id"] for b in rig.client.batches for o in b["objects"]] == stored_ids(drawing_id)
    assert len(rig.client.placed) == 450


def test_every_object_of_a_drawing_shares_one_undo_group():
    rig = batching_rig()
    place(rig, 250)
    groups = {o["props"]["group"] for b in rig.client.batches for o in b["objects"]}
    assert len(groups) == 1 and len(next(iter(groups))) == 36
    place(rig, 3)
    assert len({o["props"]["group"] for b in rig.client.batches for o in b["objects"]}) == 2


def test_a_rate_limited_batch_is_retried_not_dropped():
    rig = batching_rig()
    rig.client.rate_limit_next = 2
    _, done = place(rig, 30)
    assert done == 30 and len(rig.client.batches) == 3


def test_a_missing_route_falls_back_to_paced_single_places_and_is_remembered():
    rig = base.Rig()
    drawing_id, done = place(rig, 5)
    assert done == 5 and len(rig.client.batches) == 1 and draw.batch_state["available"] is False
    assert len(rig.client.placed) == 5
    assert len({p["props"]["group"] for p in rig.client.placed}) == 1 and len(stored_ids(drawing_id)) == 5
    place(rig, 4)
    assert len(rig.client.batches) == 1


def test_a_route_that_vanishes_midway_finishes_the_rest_one_by_one():
    rig = batching_rig()
    calls = {"n": 0}
    original = rig.client.call

    async def flaky(method, path, body=None, **kw):
        if path.endswith("/canvas/objects/batch"):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ApiError(405, "no")
        return await original(method, path, body, **kw)

    rig.client.call = flaky
    drawing_id, done = place(rig, 260)
    assert done == 260 and len(stored_ids(drawing_id)) == 260 and draw.batch_state["available"] is False
    assert len(rig.client.placed) == 260


def test_other_batch_errors_stop_the_drawing_with_what_was_placed_kept():
    rig = batching_rig()
    rig.client.batch_error = ApiError(403, "no canvas")
    try:
        place(rig, 10)
    except ApiError as err:
        assert err.status == 403
    else:
        raise AssertionError("expected ApiError")


def test_undo_removes_a_batched_drawing_in_ops_of_64_a_few_at_a_time():
    rig = batching_rig()
    drawing_id, _ = place(rig, 200)
    ids = stored_ids(drawing_id)
    asyncio.run(draw.remove_strokes(draw.bot.canvas("c-voice"), ids))
    removed = [i for op in rig.client.ops for i in op["object_ids"]]
    assert sorted(removed) == sorted(ids) and all(len(op["object_ids"]) <= 64 for op in rig.client.ops) and len(rig.client.ops) == 4


def test_a_rate_limited_remove_is_retried():
    rig = batching_rig()
    drawing_id, _ = place(rig, 100)
    state = {"limited": 1}
    original = rig.client.call

    async def limited(method, path, body=None, **kw):
        if path.endswith("/canvas/ops") and state["limited"]:
            state["limited"] -= 1
            raise ApiError(429, "slow")
        return await original(method, path, body, **kw)

    rig.client.call = limited
    asyncio.run(draw.remove_strokes(draw.bot.canvas("c-voice"), stored_ids(drawing_id)))
    assert len([i for op in rig.client.ops for i in op["object_ids"]]) == 100


def test_the_whole_photo_command_places_a_large_drawing_in_a_few_requests():
    import io

    from PIL import Image, ImageDraw

    rig = batching_rig()
    image = Image.new("RGB", (400, 300), (255, 255, 255))
    pen = ImageDraw.Draw(image)
    for i in range(0, 300, 12):
        pen.ellipse([20 + i % 90, 10 + i, 150 + i % 70, 60 + i], outline=(0, 0, 0), width=3)
    out = io.BytesIO()
    image.save(out, "PNG")
    base.sources.fetch_picture = lambda get, url: out.getvalue()
    rig.say("!draw photo a cat")
    assert len(rig.client.placed) > 40 and len(rig.client.batches) >= 1 and len(rig.client.batches) <= 4


if __name__ == "__main__":
    run_module_tests(globals())
