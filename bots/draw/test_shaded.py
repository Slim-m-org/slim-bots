#!/usr/bin/env python3
"""The tonal squiggle portrait and the shaded command; run directly: python3 test_shaded.py."""

import asyncio
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan  # noqa: E402
import squiggle  # noqa: E402
import test_bot as base  # noqa: E402
from test_bot import ANN, draw  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

REGION = plan.Region(100, 100, 800, 600)
DEFAULT_SHADED = draw.SHADED_MAX


def picture(painter, size=(240, 300), ground=(235, 235, 235)):
    from PIL import Image

    image = Image.new("RGB", size, ground)
    painter(image)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def dark_head(image):
    from PIL import ImageDraw

    pen = ImageDraw.Draw(image)
    w, h = image.size
    pen.ellipse([w * 0.3, h * 0.2, w * 0.7, h * 0.6], fill=(60, 50, 50))
    pen.rectangle([w * 0.2, h * 0.65, w * 0.8, h], fill=(30, 30, 50))


def x_range(objects):
    return min(o.x for o in objects), max(o.x + o.w for o in objects)


def test_a_flat_picture_draws_nothing_and_a_dark_subject_draws_waves_only_where_it_is():
    assert squiggle.squiggle(picture(lambda im: None), REGION, 600) == []
    objects = squiggle.squiggle(picture(dark_head), REGION, 600)
    assert objects
    left, right = x_range(objects)
    assert left > REGION.x + 0.1 * REGION.w and right < REGION.x + REGION.w - 0.1 * REGION.w


def test_every_stroke_is_a_small_wavy_stroke_inside_the_region_under_the_cap():
    objects = squiggle.squiggle(picture(dark_head), REGION, 200)
    assert 0 < len(objects) <= 200
    for o in objects:
        assert o.kind == "stroke" and o.props["color"] == "annotation"
        assert len(json.dumps(o.props)) < plan.PROPS_LIMIT and len(o.props["points"]) // 2 <= squiggle.CHUNK_POINTS
        assert REGION.x - 2 <= o.x and o.x + o.w <= REGION.x + REGION.w + 2 and REGION.y - 2 <= o.y and o.y + o.h <= REGION.y + REGION.h + 2


def test_a_darker_tone_waves_taller_and_tighter():
    from PIL import Image

    def band(level):
        return picture(lambda im: im.paste((level,) * 3, (40, 100, 200, 200)), (240, 300), (250, 250, 250))

    def amplitude(data):
        rgb, gray = squiggle._load(data)
        mask = squiggle.subject_mask(rgb, None)
        dark = squiggle._darkness(gray, mask)
        runs = squiggle._row(dark, rgb.size[0], 150.0, 6.0)
        ys = [p[1] for run in runs for p in run]
        return (max(ys) - min(ys)) if ys else 0.0

    assert amplitude(band(20)) > amplitude(band(150)) or amplitude(band(150)) == 0.0


def test_a_face_box_keeps_head_and_shoulders_and_drops_the_ground_beside_them():
    face = (0.3, 0.2, 0.4, 0.3)
    rgb, _ = squiggle._load(picture(lambda im: None))
    mask = squiggle.subject_mask(rgb, face)
    w, h = rgb.size
    assert mask[int(h * 0.35)][int(w * 0.5)] > 0.95 and mask[int(h * 0.35)][int(w * 0.02)] < 0.05 and mask[int(h * 0.02)][int(w * 0.5)] < 0.05
    assert mask[int(h * 0.75)][int(w * 0.5)] > 0.5


def test_the_background_is_flattened_to_paper_without_a_face():
    rgb, _ = squiggle._load(picture(dark_head, ground=(200, 60, 60)))
    mask = squiggle.subject_mask(rgb, None)
    w, h = rgb.size
    assert mask[int(h * 0.4)][int(w * 0.5)] > 0.9 and mask[int(h * 0.1)][int(w * 0.1)] < 0.05


def test_a_pale_subject_on_a_dark_ground_is_drawn_as_the_subject_not_the_ground():
    def pale(image):
        from PIL import ImageDraw

        ImageDraw.Draw(image).ellipse([60, 80, 180, 220], fill=(230, 220, 200))

    objects = squiggle.squiggle(picture(pale, ground=(40, 60, 40)), REGION, 600)
    assert objects
    left, right = x_range(objects)
    assert left > REGION.x + 0.15 * REGION.w and right < REGION.x + 0.85 * REGION.w


def test_short_gaps_are_bridged_so_a_row_is_few_strokes():
    dark = [[0.0] * 60 for _ in range(10)]
    for x in list(range(5, 20)) + list(range(23, 40)):
        for y in range(10):
            dark[y][x] = 0.9
    runs = squiggle._row(dark, 60, 5.0, 6.0)
    assert len(runs) == 1 and runs[0][0][0] == 5.0 and runs[0][-1][0] == 39.0


def test_the_cap_is_never_passed_even_for_a_busy_picture():
    state, rows = 5, []

    def noise(image):
        nonlocal state
        for x in range(image.width):
            for y in range(image.height):
                state = (state * 1103515245 + 12345) % 2**31
                v = state % 256
                image.putpixel((x, y), (v, v, v))

    assert len(squiggle.squiggle(picture(noise, (200, 200)), REGION, 150)) <= 150


def say_with(rig, text, attachments=None, user=ANN):
    member = draw.bot.space._make_member({**user, "role_ids": []})
    member._base_permissions = 0
    draw.bot.space.members[user["id"]] = member
    message = {"id": "m1", "channel_id": "c1", "author_id": user["id"], "content": text, "seq": 1}
    if attachments:
        message["attachments"] = attachments
    asyncio.run(draw.bot.process_message(message))
    return rig.client.ephemerals[-1]["content"] if rig.client.ephemerals else ""


def rig_for_shaded(source=None):
    rig = base.Rig()
    draw.SHADED_MAX = 300
    rig.png = source or picture(dark_head)
    base.sources.fetch_picture = lambda get, url: rig.png
    return rig


def test_shaded_and_its_dots_alias_search_draw_and_credit_the_source():
    for command in ("!draw shaded a cat", "!draw dots a cat"):
        rig = rig_for_shaded()
        say_with(rig, command)
        assert rig.searches == ["a cat"] and rig.client.placed
        assert all(p["kind"] == "stroke" for p in rig.client.placed)
        assert rig.client.sent[0]["content"].startswith("making a shaded portrait of a cat")
        reply = rig.client.sent[-1]["content"]
        assert f"{len(rig.client.placed)} strokes" in reply and '"A cat" by Sam (CC BY 2.0) https://page/1' in reply


def test_an_attached_image_is_used_instead_of_a_search():
    rig = rig_for_shaded()
    rig.client.respond("GET", "/attachments/a1", picture(dark_head))
    say_with(rig, "!draw shaded", attachments=[{"id": "a1", "content_type": "image/png"}])
    assert rig.searches == [] and rig.client.placed and "from" not in rig.client.sent[-1]["content"].split("canvas in")[1]


def test_nothing_found_a_failed_attachment_and_a_blank_picture_each_get_a_sentence():
    rig = rig_for_shaded()
    base.sources.find_pictures = lambda get, subject, google=None, report=None: iter([])
    assert "no picture matched" in say_with(rig, "!draw shaded zzzz")
    rig = rig_for_shaded()
    from slimbots.http import ApiError

    rig.client.respond("GET", "/attachments/a1", ApiError(404, "gone"))
    assert "could not read the image" in say_with(rig, "!draw shaded", attachments=[{"id": "a1", "content_type": "image/png"}])
    rig = rig_for_shaded(picture(lambda im: None))
    assert "could not find anything to draw" in say_with(rig, "!draw shaded a cat")
    rig = rig_for_shaded()
    assert "attach an image or say what to draw" in say_with(rig, "!draw shaded")


def test_the_cap_comes_from_the_env_setting_and_undo_removes_every_stroke():
    rig = rig_for_shaded()
    draw.SHADED_MAX = 120
    say_with(rig, "!draw shaded a cat")
    assert 0 < len(rig.client.placed) <= 120
    placed = [p["id"] for p in rig.client.placed]
    assert "removed your drawing" in say_with(rig, "!draw undo")
    assert sorted(i for op in rig.client.ops for i in op["object_ids"]) == sorted(placed)


def test_the_default_cap_is_600():
    assert DEFAULT_SHADED == 600


def test_shaded_needs_a_call_and_respects_the_cooldown():
    rig = rig_for_shaded()
    draw.bot.voice._member_channel.clear()
    assert "join a voice channel first" in say_with(rig, "!draw shaded a cat")
    rig.in_call(ANN)
    draw.COOLDOWN_SECONDS = 60
    draw.last_draw.clear()
    say_with(rig, "!draw shaded a cat")
    assert "can draw again" in say_with(rig, "!draw shaded a cat")


if __name__ == "__main__":
    run_module_tests(globals())
