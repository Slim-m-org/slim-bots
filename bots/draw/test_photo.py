#!/usr/bin/env python3
"""The photo line tracer: edges, linking, simplifying, the cap and the command; run directly: python3 test_photo.py."""

import asyncio
import io
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import photo  # noqa: E402
import plan  # noqa: E402
import test_bot as base  # noqa: E402
from test_bot import ANN, draw  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

REGION = plan.Region(100, 100, 800, 600)


def shot(painter, size=(240, 180)):
    from PIL import Image

    image = Image.new("RGB", size, (255, 255, 255))
    painter(image)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def box_and_circle(image):
    from PIL import ImageDraw

    pen = ImageDraw.Draw(image)
    pen.rectangle([30, 30, 110, 120], outline=(0, 0, 0), width=4)
    pen.ellipse([130, 50, 210, 130], outline=(0, 0, 0), width=4)


def test_a_clean_shape_becomes_a_few_long_simplified_lines():
    lines, width, height = photo.strokes(shot(box_and_circle), 220)
    assert (width, height) == (240, 180) or max(width, height) <= photo.WORK_SIDE
    assert 2 <= len(lines) <= 30
    assert all(len(line) >= 2 for line in lines)
    assert sum(len(line) for line in lines) < 400


def test_a_blank_photo_gives_no_strokes():
    assert photo.trace_photo(shot(lambda im: None), REGION, 220) == []


def test_every_stroke_is_a_small_inked_stroke_inside_the_region_with_the_photos_aspect():
    objects = photo.trace_photo(shot(box_and_circle), REGION, 220)
    assert objects
    for o in objects:
        assert o.kind == "stroke" and o.props["color"] == "annotation" and o.props["width"] == 2.0
        assert len(json.dumps(o.props)) < plan.PROPS_LIMIT and len(o.props["points"]) // 2 <= photo.CHUNK_POINTS
        assert REGION.x - 1 <= o.x and o.x + o.w <= REGION.x + REGION.w + 1 and REGION.y - 1 <= o.y and o.y + o.h <= REGION.y + REGION.h + 1
    left, right = min(o.x for o in objects), max(o.x + o.w for o in objects)
    top, bottom = min(o.y for o in objects), max(o.y + o.h for o in objects)
    scale = min(REGION.w / 240, REGION.h / 180)
    assert abs((left + right) / 2 - (REGION.x + REGION.w / 2)) < 6 * scale and abs((right - left) - 180 * scale) < 10 * scale
    assert abs((bottom - top) - 90 * scale) < 12 * scale


def test_the_stroke_cap_keeps_the_longest_lines_first():
    def many(image):
        from PIL import ImageDraw

        pen = ImageDraw.Draw(image)
        for i in range(12):
            pen.line([(10 + i * 18, 20), (10 + i * 18, 20 + 10 + i * 10)], fill=(0, 0, 0), width=3)

    full, _, _ = photo.strokes(shot(many), 220)
    few, _, _ = photo.strokes(shot(many), 4)
    assert len(few) <= 4 < len(full)
    longest = max(sum(abs(b[1] - a[1]) for a, b in zip(line, line[1:])) for line in full)
    assert max(sum(abs(b[1] - a[1]) for a, b in zip(line, line[1:])) for line in few) >= longest * 0.8


def test_a_long_line_is_split_into_pieces_that_share_a_joint():
    line = [(i, (i * 3) % 40) for i in range(400)]
    pieces = photo._split(line)
    assert all(len(p) <= photo.CHUNK_POINTS for p in pieces) and len(pieces) == 3
    assert pieces[0][-1] == pieces[1][0] and pieces[1][-1] == pieces[2][0]


def test_douglas_peucker_drops_collinear_points_and_keeps_corners():
    line = [(x, 0) for x in range(10)] + [(9, y) for y in range(1, 10)]
    assert photo.simplify(line) == [(0, 0), (9, 0), (9, 9)]


def test_a_long_straight_line_along_the_edge_is_dropped_but_the_same_line_inside_is_kept():
    top = [(10, 3), (300, 3)]
    assert photo.hugs_border(top, 400, 300)
    assert photo.hugs_border([(2, 20), (2, 250)], 400, 300)
    assert not photo.hugs_border([(10, 150), (300, 150)], 400, 300)
    assert not photo.hugs_border([(10, 3), (30, 3)], 400, 300)
    assert not photo.hugs_border([(10, 3), (150, 12), (300, 3)], 400, 300)


def test_a_tiny_lone_fragment_is_dropped_and_one_beside_a_line_is_kept():
    long_line = [(0, 0), (200, 0)]
    near = [(100, 6), (108, 6)]
    far = [(100, 200), (108, 200)]
    kept = photo.drop_isolated([long_line, near, far])
    assert long_line in kept and near in kept and far not in kept


def test_a_frame_along_the_picture_border_is_not_traced_but_the_subject_inside_is():
    def framed(image):
        from PIL import ImageDraw

        pen = ImageDraw.Draw(image)
        pen.rectangle([0, 0, image.width - 1, image.height - 1], outline=(0, 0, 0), width=6)
        pen.ellipse([70, 50, 170, 130], outline=(0, 0, 0), width=4)

    lines, width, height = photo.strokes(shot(framed), 220)
    assert lines and not any(photo.hugs_border(line, width, height) for line in lines)
    assert any(60 < sum(p[0] for p in line) / len(line) < width - 60 for line in lines)


def test_structure_is_found_at_a_modest_size_and_a_large_photo_traces_in_well_under_a_second_or_two():
    import time

    assert photo.WORK_SIDE == 560
    def thick(image):
        from PIL import ImageDraw

        pen = ImageDraw.Draw(image)
        pen.rectangle([300, 300, 1100, 1200], outline=(0, 0, 0), width=40)
        pen.ellipse([1300, 500, 2100, 1300], outline=(0, 0, 0), width=40)

    big = shot(thick, (2400, 1800))
    started = time.time()
    lines, width, height = photo.strokes(big, 300)
    assert max(width, height) == 560 and lines
    assert time.time() - started < 3


def test_jagged_short_fragments_are_dropped_but_a_smooth_arc_of_the_same_length_is_kept():
    from photo_quality import wiggly

    diagonal = 700
    zigzag = [(i * 4, 12 * (i % 2)) for i in range(12)]
    arc = [(40 * math.cos(t / 10), 40 * math.sin(t / 10)) for t in range(0, 16)]
    assert wiggly(zigzag, diagonal) and not wiggly(arc, diagonal)
    long_wander = [(i * 20, 30 * (i % 2)) for i in range(40)]
    assert not wiggly(long_wander, diagonal)


def test_long_straight_vertical_lines_outside_the_middle_are_background_but_inside_they_stay():
    from photo_quality import background_straight

    curtain = [(60, 20), (62, 150), (60, 300)]
    assert background_straight(curtain, 560, 420)
    assert not background_straight([(280, 20), (282, 150), (280, 300)], 560, 420)
    assert not background_straight([(60, 20), (200, 150), (60, 300)], 560, 420)
    assert not background_straight([(60, 20), (62, 40)], 560, 420)


def test_the_subject_ellipse_ranks_central_lines_over_outer_ones_and_marks_far_ones_as_background():
    from photo_quality import SUBJECT_REACH, subject_distance, subject_weight

    centre, corner = [(270, 150), (290, 190)], [(5, 400), (20, 410)]
    assert subject_weight(centre, 560, 420) == 1.0 and subject_weight(corner, 560, 420) < 0.5
    assert subject_distance(corner, 560, 420) > SUBJECT_REACH


def test_hysteresis_keeps_weak_pixels_only_when_they_touch_a_strong_one():
    weak = {(1, 1): 5.0, (2, 1): 5.0, (3, 1): 5.0, (10, 10): 5.0, (11, 10): 5.0, (4, 1): 50.0}
    kept = photo._hysteresis(weak, 40.0, photo.quality.default_face(100, 100))
    assert set(kept) == {(1, 1), (2, 1), (3, 1), (4, 1)}


def test_chaikin_rounds_corners_and_keeps_the_end_points():
    from photo_quality import chaikin

    line = [(0, 0), (10, 0), (10, 10)]
    rounded = chaikin(line)
    assert rounded[0] == (0, 0) and rounded[-1] == (10, 10) and (10, 0) not in rounded and len(rounded) == 6


def test_a_huge_budget_is_not_padded_with_junk_lines():
    lines, _, _ = photo.strokes(shot(box_and_circle), 1500)
    assert len(lines) < 60


def say_with(rig, text, attachments=None, user=ANN):
    member = draw.bot.space._make_member({**user, "role_ids": []})
    member._base_permissions = 0
    draw.bot.space.members[user["id"]] = member
    message = {"id": "m1", "channel_id": "c1", "author_id": user["id"], "content": text, "seq": 1}
    if attachments:
        message["attachments"] = attachments
    asyncio.run(draw.bot.process_message(message))
    return rig.client.ephemerals[-1]["content"] if rig.client.ephemerals else ""


def rig_for_photo():
    rig = base.Rig()
    rig.png = shot(box_and_circle)
    base.sources.fetch_picture = lambda get, url: rig.png
    return rig


def test_the_photo_command_searches_traces_places_strokes_and_credits_the_source():
    rig = rig_for_photo()
    say_with(rig, "!draw photo a cat")
    assert rig.searches == ["a cat"] and rig.client.placed
    assert all(p["kind"] == "stroke" and p["props"]["color"] == "annotation" for p in rig.client.placed)
    assert rig.client.sent[0]["content"].startswith("tracing a line drawing of a cat")
    reply = rig.client.sent[-1]["content"]
    assert f"{len(rig.client.placed)} strokes" in reply and 'from "A cat" by Sam (CC BY 2.0) https://page/1' in reply


def test_an_attached_image_is_traced_without_a_search_and_undo_removes_it():
    rig = rig_for_photo()
    rig.client.respond("GET", "/attachments/a1", shot(box_and_circle))
    say_with(rig, "!draw photo", attachments=[{"id": "a1", "content_type": "image/png"}])
    assert rig.searches == [] and rig.client.placed and "from" not in rig.client.sent[-1]["content"].split("canvas in")[1]
    placed = [p["id"] for p in rig.client.placed]
    assert "removed your drawing" in say_with(rig, "!draw undo")
    assert [i for op in rig.client.ops for i in op["object_ids"]] == placed


def test_a_blank_photo_and_no_subject_each_get_a_sentence():
    rig = rig_for_photo()
    rig.png = shot(lambda im: None)
    assert "could not find anything to draw" in say_with(rig, "!draw photo a cat")
    rig = rig_for_photo()
    assert "attach an image or say what to draw" in say_with(rig, "!draw photo")


def test_the_help_text_lists_every_mode():
    text = draw.draw.help if hasattr(draw.draw, "help") else ""
    for mode in ("photo <subject>", "dots <subject>", "trace <word>", "undo", "clear"):
        assert mode in text, mode


if __name__ == "__main__":
    run_module_tests(globals())
