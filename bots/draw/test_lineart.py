#!/usr/bin/env python3
"""Face shading, the face box from the crop call, and refusing explicit requests and pictures; run directly: python3 test_lineart.py."""

import asyncio
import contextlib
import io
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crop  # noqa: E402
import photo  # noqa: E402
import photo_quality as quality  # noqa: E402
import photo_shade  # noqa: E402
import plan  # noqa: E402
import safety  # noqa: E402
import test_bot as base  # noqa: E402
from test_bot import ANN, draw  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

REGION = plan.Region(100, 100, 800, 600)


def portrait(size=(360, 480)):
    """A pale face on a mid-grey ground with dark eyes, a dark mouth and dark hair across the top."""
    from PIL import Image, ImageDraw, ImageFilter

    image = Image.new("RGB", size, (140, 140, 140))
    pen = ImageDraw.Draw(image)
    w, h = size
    pen.ellipse([w * 0.25, h * 0.15, w * 0.75, h * 0.8], fill=(225, 190, 165))
    pen.ellipse([w * 0.25, h * 0.1, w * 0.75, h * 0.35], fill=(40, 30, 25))
    pen.ellipse([w * 0.34, h * 0.4, w * 0.44, h * 0.45], fill=(30, 20, 20))
    pen.ellipse([w * 0.56, h * 0.4, w * 0.66, h * 0.45], fill=(30, 20, 20))
    pen.ellipse([w * 0.4, h * 0.62, w * 0.6, h * 0.68], fill=(90, 30, 40))
    out = io.BytesIO()
    image.filter(ImageFilter.GaussianBlur(1.5)).save(out, "PNG")
    return out.getvalue()


FACE = (0.22, 0.12, 0.56, 0.7)


def test_the_face_ellipse_follows_the_given_box_and_the_default_sits_mid_picture():
    face = quality.face_from_box((0.25, 0.1, 0.5, 0.6), 400, 500)
    assert (face.cx, face.cy) == (200, 200) and face.contains((200, 200)) and not face.contains((10, 10))
    assert abs(face.rx - 0.62 * 0.5 * 400) < 1e-6
    default = quality.default_face(400, 500)
    assert default.contains((200, 210)) and not default.contains((200, 490))
    assert face.share([(200, 200), (10, 10)]) == 0.5


def test_shading_darkens_the_features_and_stays_inside_the_face():
    from PIL import Image

    gray = Image.open(io.BytesIO(portrait())).convert("L")
    face = quality.face_from_box(FACE, *gray.size)
    lines = photo_shade.shade_lines(gray, face)
    assert lines
    points = [p for line in lines for p in line]
    assert all(face.contains(p, 1.05) for p in points)
    eyes = [p for p in points if abs(p[1] - 0.425 * gray.height) < 0.05 * gray.height and (0.3 < p[0] / gray.width < 0.7)]
    cheek = [p for p in points if abs(p[1] - 0.52 * gray.height) < 0.03 * gray.height and 0.42 < p[0] / gray.width < 0.58]
    assert len(eyes) > 3 * max(len(cheek), 1)


def test_neighbouring_hatch_runs_are_joined_into_one_zigzag():
    runs = [((0, 0), (10, 10)), ((4, 0), (14, 10)), ((8, 0), (18, 10)), ((200, 0), (210, 10))]
    lines = photo_shade._zigzag(runs)
    assert len(lines) == 2 and len(lines[0]) == 6 and len(lines[1]) == 2
    assert lines[0][1] == (10, 10) and lines[0][2] == (14, 10)


def test_a_face_box_adds_shading_and_without_one_the_trace_is_edges_only():
    plain = photo.trace_photo(portrait(), REGION, 300)
    shaded = photo.trace_photo(portrait(), REGION, 300, FACE)
    assert plain and len(shaded) > len(plain)
    assert all(len(__import__("json").dumps(o.props)) < plan.PROPS_LIMIT and len(o.props["points"]) // 2 <= photo.CHUNK_POINTS for o in shaded)
    assert len(photo.trace_photo(portrait(), REGION, 40, FACE)) <= 40


def test_a_face_in_the_crop_is_re_expressed_in_the_croppeds_own_fractions():
    got = crop.face_in_crop({"x": 0.4, "y": 0.1, "w": 0.2, "h": 0.2}, (0.2, 0.0, 0.6, 0.5))
    assert [round(v, 6) for v in got] == [round(v, 6) for v in ((0.4 - 0.2) / 0.6, 0.2, 0.2 / 0.6, 0.4)]
    assert crop.face_in_crop({"x": 0.0, "y": 0.9, "w": 0.1, "h": 0.05}, (0.2, 0.0, 0.6, 0.5)) is None
    assert crop.face_in_crop("nope", (0, 0, 1, 1)) is None
    x, y, w, h = crop.face_in_crop({"x": 0.1, "y": 0.1, "w": 0.9, "h": 0.9}, (0.0, 0.0, 0.5, 0.5))
    assert x + w <= 1.0 and y + h <= 1.0


def test_an_answer_carries_the_kind_the_face_and_the_explicit_flag():
    raw = {"x": 0.2, "y": 0.0, "w": 0.6, "h": 0.5, "kind": "person", "face": {"x": 0.4, "y": 0.05, "w": 0.2, "h": 0.2}, "explicit": False}
    answer = crop.parse_answer(raw)
    assert answer.box and answer.face and answer.kind == "person" and answer.explicit is False
    assert crop.parse_answer({**raw, "kind": "animal"}).face is None
    assert crop.parse_answer({**raw, "kind": "bogus"}).kind == "other"
    flagged = crop.parse_answer({"explicit": True})
    assert flagged.explicit is True and flagged.box is None
    assert crop.parse_answer(None).explicit is False and crop.parse_answer("x").box is None


def test_the_crop_tool_asks_for_kind_face_and_explicit():
    import ai

    props = ai.CROP_TOOL["input_schema"]["properties"]
    assert {"kind", "face", "explicit"} <= set(props) and "explicit" in ai.CROP_TOOL["input_schema"]["required"]
    assert "nudity" in ai.CROP_SYSTEM


def test_explicit_requests_are_caught_and_ordinary_words_are_not():
    for text in ("barack obama naked", "a NUDE model", "taylor swift topless", "nsfw art", "sexy pose", "porn", "gory battlefield", "masturbation"):
        assert safety.is_explicit(text), text
    for text in ("essex", "a cat", "cumbersome", "scunthorpe", "document", "sussex", "assessment", "barack obama", "rape field flowers", "classic"):
        assert not safety.is_explicit(text), text


def say_log(rig, text):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rig.say(text)
    return [line for line in out.getvalue().splitlines() if line.startswith("draw ")]


def test_every_mode_refuses_an_explicit_prompt_privately_and_logs_it():
    class Planner:
        async def plan(self, *a, **k):
            raise AssertionError("the model must not be asked")

    for planner in (None, Planner()):
        for command in ("!draw", "!draw photo", "!draw dots", "!draw trace"):
            rig = base.Rig()
            draw.planner = planner
            lines = say_log(rig, f"{command} barack obama naked")
            assert safety.REFUSAL in rig.client.ephemerals[-1]["content"], (command, planner)
            assert not rig.client.placed and not rig.client.sent
            assert lines and 'outcome="refused: explicit prompt"' in lines[-1]


class Flagging:
    def __init__(self, answer):
        self.answer = answer

    async def locate(self, subject, picture, media_type):
        return self.answer


def photo_rig(planner):
    rig = base.Rig()
    draw.planner, draw.AI_DAILY_CAP = planner, 100
    rig.png = portrait()
    base.sources.fetch_picture = lambda get, url: rig.png
    return rig


def test_a_picture_the_crop_call_flags_explicit_is_refused_before_anything_is_drawn():
    rig = photo_rig(Flagging({"x": 0.1, "y": 0.1, "w": 0.8, "h": 0.8, "kind": "person", "explicit": True}))
    lines = say_log(rig, "!draw photo a person")
    assert not rig.client.placed and "looks explicit" in rig.client.ephemerals[-1]["content"]
    assert 'outcome="refused: explicit picture"' in lines[-1]
    rig = photo_rig(Flagging({"explicit": True}))
    say_log(rig, "!draw dots a person")
    assert not rig.client.placed and "looks explicit" in rig.client.ephemerals[-1]["content"]


def test_the_models_face_box_reaches_the_tracer_and_is_logged():
    answer = {"x": 0.1, "y": 0.05, "w": 0.8, "h": 0.85, "kind": "person", "face": {"x": 0.3, "y": 0.15, "w": 0.4, "h": 0.5}, "explicit": False}
    rig = photo_rig(Flagging(answer))
    faces = []
    original = draw.photo.trace_photo
    draw.photo.trace_photo = lambda data, free, cap, face=None: faces.append(face) or original(data, free, cap, face)
    try:
        lines = say_log(rig, "!draw photo a person")
    finally:
        draw.photo.trace_photo = original
    assert faces and faces[0] is not None and len(faces[0]) == 4 and "face=" in lines[-1] and rig.client.placed


def test_without_a_key_there_is_no_face_box_and_nothing_is_flagged():
    rig = photo_rig(None)
    faces = []
    original = draw.photo.trace_photo
    draw.photo.trace_photo = lambda data, free, cap, face=None: faces.append(face) or original(data, free, cap, face)
    try:
        say_log(rig, "!draw photo a person")
    finally:
        draw.photo.trace_photo = original
    assert faces == [None] and rig.client.placed


if __name__ == "__main__":
    run_module_tests(globals())
