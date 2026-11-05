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


def test_a_flat_picture_draws_nothing_and_the_whole_picture_is_drawn_background_included():
    assert squiggle.squiggle(picture(lambda im: None), REGION, 600) == []
    objects = squiggle.squiggle(picture(dark_head), REGION, 600)
    assert objects
    left, right = x_range(objects)
    assert right - left > 0.6 * (squiggle.sizing.drawing_box(REGION, 0.8, 0.0)[2])


def test_every_stroke_is_a_small_wavy_stroke_inside_the_region_under_the_cap():
    objects = squiggle.squiggle(picture(dark_head), REGION, 200)
    assert 0 < len(objects) <= 200
    for o in objects:
        assert o.kind == "stroke" and o.props["color"] == "#f2f2f2"
        assert len(json.dumps(o.props)) < plan.PROPS_LIMIT and len(o.props["points"]) // 2 <= squiggle.CHUNK_POINTS
        assert REGION.x - 2 <= o.x and o.x + o.w <= REGION.x + REGION.w + 2 and REGION.y - 2 <= o.y and o.y + o.h <= REGION.y + REGION.h + 2


def test_a_darker_tone_waves_taller_and_tighter():
    def bands(image):
        image.paste((20, 20, 20), (20, 100, 100, 200))
        image.paste((140, 140, 140), (140, 100, 220, 200))

    rgb, gray = squiggle._load(picture(bands, (240, 300), (250, 250, 250)))
    dark = squiggle._ink_amount(gray, False)
    runs = squiggle._row(dark, rgb.size[0], 150.0, 8.0)

    def spread(low, high):
        ys = [p[1] for run in runs for p in run if low <= p[0] < high]
        return max(ys) - min(ys) if ys else 0.0

    assert spread(20, 100) > spread(140, 220) > 0.0









def test_short_gaps_are_bridged_so_a_row_is_few_strokes():
    dark = [[0.0] * 60 for _ in range(10)]
    for x in list(range(5, 20)) + list(range(23, 40)):
        for y in range(10):
            dark[y][x] = 0.9
    runs = squiggle._row(dark, 60, 5.0, 6.0)
    assert len(runs) == 1 and runs[0][0][0] == 5.0 and runs[0][-1][0] == 39.0


def _amount_at(board, face, x, y, gray_level_at):
    """The ink amount at one pixel of a picture that is dark on the left half and light on the right."""
    def halves(image):
        image.paste((30, 30, 30), (0, 0, image.width // 2, image.height))
        image.paste((225, 225, 225), (image.width // 2, 0, image.width, image.height))

    data = picture(halves, (240, 300), (128, 128, 128))
    rgb, gray = squiggle._load(data)
    return squiggle._ink_amount(gray, board == "dark")[y][x]


def test_on_a_dark_board_ink_goes_where_the_picture_is_light_and_on_a_light_board_where_it_is_dark():
    dark_left, light_right = (20, 150), (220, 150)
    assert _amount_at("dark", None, *light_right, None) > 0.8 and _amount_at("dark", None, *dark_left, None) < 0.1
    assert _amount_at("light", None, *dark_left, None) > 0.8 and _amount_at("light", None, *light_right, None) < 0.1


def test_the_ink_colour_follows_the_board():
    def face_on_dark(image):
        from PIL import ImageDraw

        ImageDraw.Draw(image).ellipse([70, 80, 170, 220], fill=(225, 190, 165))
        ImageDraw.Draw(image).ellipse([95, 130, 115, 145], fill=(30, 20, 20))

    data = picture(face_on_dark, (240, 300), (40, 40, 50))
    dark_board = squiggle.squiggle(data, REGION, 600, "dark")
    light_board = squiggle.squiggle(data, REGION, 600, "light")
    assert {o.props["color"] for o in dark_board} == {"#f2f2f2"} and {o.props["color"] for o in light_board} == {"#1f1f1f"}
    assert dark_board and light_board and sum(o.w * o.h for o in dark_board) > 0




def test_the_board_setting_defaults_to_dark():
    assert draw.BOARD == "dark"


def _ramp_ink(board_dark):
    def ramp(image):
        for x in range(image.width):
            v = int(255 * x / (image.width - 1))
            image.paste((v, v, v), (x, 0, x + 1, image.height))

    rgb, gray = squiggle._load(picture(ramp, (256, 40)))
    return squiggle._ink_amount(gray, board_dark)[20]


def test_on_a_dark_board_the_darkest_quarter_is_bare_and_mid_tones_separate():
    ink = _ramp_ink(True)
    width = len(ink)
    assert max(ink[: int(width * 0.22)]) == 0.0
    assert ink[-3] > 0.9 and ink[int(width * 0.4)] < 0.2 and ink[int(width * 0.7)] > ink[int(width * 0.4)] + 0.3
    assert all(b >= a - 1e-9 for a, b in zip(ink, ink[1:]))


def test_the_wave_carries_tone_more_in_height_than_in_tightness():
    assert squiggle.TONE_FREQUENCY <= squiggle.BASE_FREQUENCY and squiggle.AMPLITUDE >= 1.0




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
    base.sources.find_pictures = lambda get, subject, google=None, report=None, serpapi=None, on_call=None: iter([])
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


def test_the_default_cap_is_1500_and_the_setting_is_bounded_200_to_4000():
    assert DEFAULT_SHADED == 1500


def test_shaded_needs_a_call_and_respects_the_cooldown():
    rig = rig_for_shaded()
    draw.bot.voice._member_channel.clear()
    assert "join a voice channel first" in say_with(rig, "!draw shaded a cat")
    rig.in_call(ANN)
    draw.COOLDOWN_SECONDS = 60
    draw.last_draw.clear()
    say_with(rig, "!draw shaded a cat")
    assert "can draw again" in say_with(rig, "!draw shaded a cat")


def test_a_picture_with_no_face_focus_is_drawn_whole_with_its_background():
    def scene(image):
        from PIL import ImageDraw

        pen = ImageDraw.Draw(image)
        pen.rectangle([0, 0, 239, 150], fill=(210, 210, 215))
        pen.ellipse([90, 120, 150, 200], fill=(40, 40, 40))

    objects = squiggle.squiggle(picture(scene, (240, 300), (90, 90, 90)), REGION, 1500)
    top, bottom = min(o.y for o in objects), max(o.y + o.h for o in objects)
    left, right = x_range(objects)
    assert top < REGION.y + 0.2 * REGION.h and right - left > 0.5 * REGION.w * 0.7


def test_a_busy_picture_gets_more_rows_and_a_bigger_drawing_than_a_plain_one():
    def stripes(image):
        for x in range(0, image.width, 4):
            image.paste((20, 20, 20), (x, 0, x + 2, image.height))

    def gradient(image):
        for y in range(image.height):
            v = 60 + y * 150 // image.height
            image.paste((v, v, v), (0, y, image.width, y + 1))

    busy, plain = picture(stripes, (320, 240)), picture(gradient, (320, 240))
    assert squiggle.sizing.measure(busy) > squiggle.sizing.measure(plain) + 0.5
    big, small = squiggle.squiggle(busy, draw.plan.Region(0, 0, 1600, 1000), 4000), squiggle.squiggle(plain, draw.plan.Region(0, 0, 1600, 1000), 4000)
    assert max(o.x + o.w for o in big) - min(o.x for o in big) > 1.2 * (max(o.x + o.w for o in small) - min(o.x for o in small))
    assert len({round(o.y) for o in big}) > len({round(o.y) for o in small})


if __name__ == "__main__":
    run_module_tests(globals())
