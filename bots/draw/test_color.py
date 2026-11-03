#!/usr/bin/env python3
"""The colour brushwork mode and the paste mode; run directly: python3 test_color.py."""

import asyncio
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import colorize  # noqa: E402
import paste  # noqa: E402
import plan  # noqa: E402
import test_bot as base  # noqa: E402
from test_bot import ANN, draw  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

REGION = plan.Region(100, 100, 800, 600)
DEFAULT_COLOR = draw.COLOR_MAX


def picture(painter, size=(300, 200)):
    from PIL import Image

    image = Image.new("RGB", size, (250, 250, 250))
    painter(image)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def three_bands(image):
    w, h = image.size
    image.paste((200, 30, 30), (0, 0, w // 3, h))
    image.paste((30, 160, 40), (w // 3, 0, 2 * w // 3, h))
    image.paste((30, 40, 200), (2 * w // 3, 0, w, h))


def test_each_stroke_is_one_run_of_one_hex_colour_and_a_long_run_is_one_stroke():
    objects = colorize.color_strokes(picture(three_bands), REGION, 2000)
    assert objects
    colours = {o.props["color"] for o in objects}
    assert len(colours) <= 4 and all(c.startswith("#") and len(c) == 7 for c in colours)
    rows = {round(o.y) for o in objects}
    assert len(objects) <= 3 * len(rows) + 3
    for o in objects:
        assert o.kind == "stroke" and o.props["points"][1] == 0.0 and len(json.dumps(o.props)) < plan.PROPS_LIMIT


def test_rows_touch_so_the_stroke_width_covers_the_row_pitch():
    objects = colorize.color_strokes(picture(three_bands), REGION, 2000)
    ys = sorted({round(o.y, 3) for o in objects})
    pitch = ys[1] - ys[0]
    assert all(o.props["width"] >= pitch for o in objects) and all(o.props["width"] <= pitch * 1.5 for o in objects)


def test_the_whole_picture_is_covered_with_its_aspect_ratio_and_stays_inside_the_region():
    objects = colorize.color_strokes(picture(three_bands, (300, 200)), REGION, 2000)
    left, right = min(o.x - o.props["width"] / 2 for o in objects), max(o.x + o.w + o.props["width"] / 2 for o in objects)
    top, bottom = min(o.y - o.props["width"] / 2 for o in objects), max(o.y + o.props["width"] / 2 for o in objects)
    assert abs((right - left) / (bottom - top) - 1.5) < 0.08
    assert left >= REGION.x - 12 and right <= REGION.x + REGION.w + 12 and top >= REGION.y - 12 and bottom <= REGION.y + REGION.h + 12


def test_the_stroke_count_never_passes_the_budget_and_a_bigger_budget_gives_a_finer_picture():
    def noisy(image):
        state = 7
        for x in range(image.width):
            for y in range(image.height):
                state = (state * 1103515245 + 12345) % 2**31
                image.putpixel((x, y), ((state >> 4) % 256, (state >> 12) % 256, (state >> 20) % 256))

    small = colorize.color_strokes(picture(noisy), REGION, 300)
    large = colorize.color_strokes(picture(noisy), REGION, 3000)
    assert 0 < len(small) <= 300 and len(small) < len(large) <= 3000
    assert len({round(o.y, 2) for o in large}) > len({round(o.y, 2) for o in small})


def test_a_dot_sized_run_still_draws_as_a_round_dot():
    def speck(image):
        image.putpixel((150, 100), (0, 0, 0))
        image.paste((0, 0, 0), (140, 90, 160, 110))

    objects = colorize.color_strokes(picture(speck), REGION, 2000)
    assert objects and all(o.w >= 0.5 for o in objects)


def say_with(rig, text, attachments=None, user=ANN):
    member = draw.bot.space._make_member({**user, "role_ids": []})
    member._base_permissions = 0
    draw.bot.space.members[user["id"]] = member
    message = {"id": "m1", "channel_id": "c1", "author_id": user["id"], "content": text, "seq": 1}
    if attachments:
        message["attachments"] = attachments
    asyncio.run(draw.bot.process_message(message))
    return rig.client.ephemerals[-1]["content"] if rig.client.ephemerals else ""


def rig_for(command_image=None):
    rig = base.Rig()
    draw.COLOR_MAX = 600
    rig.png = command_image or picture(three_bands)
    base.sources.fetch_picture = lambda get, url: rig.png
    rig.client.batch_error = None
    return rig


def test_the_color_command_paints_the_whole_picture_in_batches_and_credits_the_source():
    rig = rig_for()
    say_with(rig, "!draw color a cat")
    assert rig.searches == ["a cat"] and 0 < len(rig.client.placed) <= 600
    assert all(p["props"]["color"].startswith("#") for p in rig.client.placed)
    assert rig.client.batches and all(len(b["objects"]) <= 200 for b in rig.client.batches)
    assert rig.client.sent[0]["content"].startswith("painting a cat in colour")
    reply = rig.client.sent[-1]["content"]
    assert f"the whole picture in colour with {len(rig.client.placed)} strokes" in reply and '"A cat" by Sam (CC BY 2.0) https://page/1' in reply


def test_color_uses_the_attachment_and_does_not_crop_to_a_face():
    rig = rig_for()
    rig.client.respond("GET", "/attachments/a1", picture(three_bands))
    original = draw.crop.choose
    draw.crop.choose = lambda data: (_ for _ in ()).throw(AssertionError("color must not crop"))
    try:
        say_with(rig, "!draw color", attachments=[{"id": "a1", "content_type": "image/png"}])
    finally:
        draw.crop.choose = original
    assert rig.searches == [] and rig.client.placed


def test_the_color_budget_comes_from_the_env_setting_default_2000_within_200_and_4000():
    assert DEFAULT_COLOR == 2000
    rig = rig_for()
    draw.COLOR_MAX = 250
    say_with(rig, "!draw color a cat")
    assert 0 < len(rig.client.placed) <= 250


def test_paste_prepares_a_small_jpeg_and_fits_it_into_the_region_with_its_aspect():
    from PIL import Image

    big = io.BytesIO()
    Image.new("RGB", (4000, 2000), (9, 90, 200)).save(big, "PNG")
    jpeg, w, h = paste.prepare(big.getvalue())
    assert jpeg.startswith(b"\xff\xd8") and (w, h) == (1600, 800)
    obj = paste.image_object("abc", w, h, REGION)
    assert obj.kind == "image" and obj.props == {"attachment": "abc"} and abs(obj.w / obj.h - 2.0) < 1e-9
    assert REGION.x <= obj.x and obj.x + obj.w <= REGION.x + REGION.w + 1e-9 and REGION.y <= obj.y and obj.y + obj.h <= REGION.y + REGION.h + 1e-9
    assert abs((obj.x + obj.w / 2) - (REGION.x + REGION.w / 2)) < 1e-9


def test_paste_uploads_the_picture_and_places_one_image_object_with_its_attachment():
    rig = rig_for(picture(three_bands, (400, 200)))
    rig.client.respond("POST", "/attachments?filename=draw.jpg", {"id": "sha256abc", "content_type": "image/jpeg", "size": 1000})
    say_with(rig, "!draw paste a cat")
    assert len(rig.client.placed) == 1
    placed = rig.client.placed[0]
    assert placed["kind"] == "image" and placed["props"]["attachment"] == "sha256abc" and placed["props"].get("group")
    uploads = [c for c in rig.client.calls if c[0] == "POST" and c[1].startswith("/attachments")]
    assert len(uploads) == 1
    assert rig.client.sent[0]["content"].startswith("pasting a cat")
    assert "the picture as 1 image" in rig.client.sent[-1]["content"] and '"A cat" by Sam' in rig.client.sent[-1]["content"]


def test_a_failed_upload_is_a_private_sentence_and_nothing_is_placed():
    from slimbots.http import ApiError

    rig = rig_for()
    rig.client.respond("POST", "/attachments?filename=draw.jpg", ApiError(413, "too large"))
    assert "could not upload the picture (http 413)" in say_with(rig, "!draw paste a cat")
    assert not rig.client.placed


def test_paste_and_color_undo_like_any_other_drawing():
    rig = rig_for()
    rig.client.respond("POST", "/attachments?filename=draw.jpg", {"id": "sha1"})
    say_with(rig, "!draw paste a cat")
    placed = [p["id"] for p in rig.client.placed]
    assert "removed your drawing" in say_with(rig, "!draw undo")
    assert [i for op in rig.client.ops for i in op["object_ids"]] == placed


if __name__ == "__main__":
    run_module_tests(globals())
