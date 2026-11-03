#!/usr/bin/env python3
"""The halftone dot portrait: grid sampling, the cap, blanks, the image source and credit; run directly: python3 test_dots.py."""

import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import halftone  # noqa: E402
import plan  # noqa: E402
import test_bot as base  # noqa: E402
from test_bot import ANN, draw  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

REGION = plan.Region(100, 100, 800, 600)


def picture(draw_fn, size=(160, 120)):
    from PIL import Image

    image = Image.new("RGB", size, (255, 255, 255))
    draw_fn(image)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def dark_disc(image):
    from PIL import ImageDraw

    w, h = image.size
    ImageDraw.Draw(image).ellipse([w * 0.25, h * 0.2, w * 0.75, h * 0.8], fill=(20, 40, 160))


def gradient(image):
    w, h = image.size
    for x in range(w):
        for y in range(h):
            level = int(255 * x / (w - 1))
            image.putpixel((x, y), (level, level, level))


def rig_for_dots(source=None):
    rig = base.Rig()
    draw.DOTS_MAX = 600
    rig.png = source or picture(dark_disc)
    base.sources.fetch_picture = lambda get, url: rig.png
    return rig


def test_a_blank_picture_makes_no_dots_and_the_background_is_skipped():
    from PIL import Image

    blank = io.BytesIO()
    Image.new("RGB", (80, 60), (255, 255, 255)).save(blank, "PNG")
    assert halftone.halftone(blank.getvalue(), REGION, 600) == []
    dots = halftone.halftone(picture(dark_disc), REGION, 600)
    xs = [d.x for d in dots]
    assert dots and min(xs) > REGION.x + 0.2 * REGION.w and max(xs) < REGION.x + 0.8 * REGION.w


def test_the_dot_count_never_passes_the_cap_and_a_small_cap_still_covers_the_picture():
    full = halftone.halftone(picture(lambda im: im.paste((0, 0, 0), (0, 0, *im.size))), REGION, 600)
    assert 0 < len(full) <= 600
    few = halftone.halftone(picture(lambda im: im.paste((0, 0, 0), (0, 0, *im.size))), REGION, 80)
    assert 0 < len(few) <= 80
    xs = [d.x for d in few]
    assert max(xs) - min(xs) > 0.6 * REGION.w


def test_darker_cells_get_bigger_dots_and_every_dot_stays_inside_the_region():
    dots = halftone.halftone(picture(gradient, (200, 40)), REGION, 600)
    by_x = sorted(dots, key=lambda d: d.x)
    assert by_x[0].props["width"] > by_x[-1].props["width"]
    for d in dots:
        assert REGION.x - 2 <= d.x and d.x + d.w <= REGION.x + REGION.w + 2 and REGION.y - 2 <= d.y and d.y + d.h <= REGION.y + REGION.h + 2


def test_each_dot_is_a_small_closed_stroke_with_a_hex_colour():
    dots = halftone.halftone(picture(dark_disc), REGION, 300)
    first = dots[0]
    assert first.kind == "stroke" and first.props["color"].startswith("#")
    points = first.props["points"]
    assert points[:2] == points[-2:] and len(str(first.props)) < 400 and first.props["width"] >= halftone.MIN_DOT
    assert len(first.props["color"]) == 7


def test_the_picture_is_fitted_with_its_aspect_ratio_and_centred():
    dots = halftone.halftone(picture(lambda im: im.paste((0, 0, 0), (0, 0, *im.size)), (200, 50)), REGION, 600)
    top, bottom = min(d.y for d in dots), max(d.y + d.h for d in dots)
    assert (bottom - top) < 0.4 * REGION.h
    assert abs((top + bottom) / 2 - (REGION.y + REGION.h / 2)) < 0.05 * REGION.h


def say_with(rig, text, attachments=None, user=ANN):
    member = draw.bot.space._make_member({**user, "role_ids": []})
    member._base_permissions = 0
    draw.bot.space.members[user["id"]] = member
    message = {"id": "m1", "channel_id": "c1", "author_id": user["id"], "content": text, "seq": 1}
    if attachments:
        message["attachments"] = attachments
    asyncio.run(draw.bot.process_message(message))
    return rig.client.ephemerals[-1]["content"] if rig.client.ephemerals else ""


def test_a_subject_is_searched_drawn_and_credited():
    rig = rig_for_dots()
    say_with(rig, "!draw dots a cat")
    assert rig.searches == ["a cat"] and rig.client.placed
    assert all(p["kind"] == "stroke" and p["props"]["color"].startswith("#") for p in rig.client.placed)
    reply = rig.client.sent[-1]["content"]
    assert f"{len(rig.client.placed)} dots" in reply and 'from "A cat" by Sam (CC BY 2.0) https://page/1' in reply


def test_an_attached_image_is_used_instead_of_a_search_and_is_not_credited():
    rig = rig_for_dots()
    rig.client.respond("GET", "/attachments/a1", picture(dark_disc))
    say_with(rig, "!draw dots", attachments=[{"id": "a1", "content_type": "image/png"}])
    assert rig.searches == [] and rig.client.placed
    assert "dot portrait" in rig.client.sent[-1]["content"] and "from" not in rig.client.sent[-1]["content"]
    say_with(rig, "!draw dots a cat", attachments=[{"id": "a1", "content_type": "image/png"}])
    assert rig.searches == []


def test_a_non_image_attachment_is_ignored_and_with_nothing_to_go_on_the_reply_says_how():
    rig = rig_for_dots()
    reply = say_with(rig, "!draw dots", attachments=[{"id": "d1", "content_type": "application/pdf"}])
    assert "attach an image or say what to draw" in reply and not rig.client.placed


def test_nothing_found_a_failed_download_and_a_blank_picture_each_get_a_sentence():
    rig = rig_for_dots()
    base.sources.search = lambda get, query, limit=8: []
    assert "no freely licensed picture" in say_with(rig, "!draw dots zzzz")
    rig = rig_for_dots()
    from slimbots.http import ApiError

    rig.client.respond("GET", "/attachments/a1", ApiError(404, "gone"))
    assert "could not read the image" in say_with(rig, "!draw dots", attachments=[{"id": "a1", "content_type": "image/png"}])
    rig = rig_for_dots()
    from PIL import Image

    blank = io.BytesIO()
    Image.new("RGB", (80, 60), (250, 250, 250)).save(blank, "PNG")
    rig.png = blank.getvalue()
    assert "too light" in say_with(rig, "!draw dots a cat")


def test_the_env_cap_bounds_the_drawing_and_undo_removes_every_dot():
    rig = rig_for_dots(picture(lambda im: im.paste((0, 0, 0), (0, 0, *im.size))))
    draw.DOTS_MAX = 120
    say_with(rig, "!draw dots a cat")
    assert 0 < len(rig.client.placed) <= 120
    placed = [p["id"] for p in rig.client.placed]
    assert "removed your drawing" in say_with(rig, "!draw undo")
    assert [i for op in rig.client.ops for i in op["object_ids"]] == placed and all(len(op["object_ids"]) <= 64 for op in rig.client.ops)


def test_dots_wait_for_a_call_and_respect_the_cooldown():
    rig = rig_for_dots()
    draw.bot.voice._member_channel.clear()
    assert "join a voice channel first" in say_with(rig, "!draw dots a cat")
    rig.in_call(ANN)
    draw.COOLDOWN_SECONDS = 60
    draw.last_draw.clear()
    say_with(rig, "!draw dots a cat")
    assert "can draw again" in say_with(rig, "!draw dots a cat")


if __name__ == "__main__":
    run_module_tests(globals())
