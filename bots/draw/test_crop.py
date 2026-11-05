#!/usr/bin/env python3
"""Cropping to the subject without a model: a detected face, else the busiest region, else everything; run directly: python3 test_crop.py."""

import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import crop  # noqa: E402
import face  # noqa: E402
import test_bot as base  # noqa: E402
from test_bot import draw  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402


def picture(painter, size=(300, 400), ground=(255, 255, 255)):
    from PIL import Image

    image = Image.new("RGB", size, ground)
    painter(image)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def framed(image):
    from PIL import ImageDraw

    pen = ImageDraw.Draw(image)
    for i in range(0, 120, 6):
        pen.line([(60, 120 + i), (250, 120 + i)], fill=(90, 40, 40), width=2)
        pen.line([(60 + i, 100), (60 + i, 260)], fill=(30, 30, 90), width=2)


@contextlib.contextmanager
def faces(answer):
    original = face.largest_face
    face.largest_face = lambda data: answer
    try:
        yield
    finally:
        face.largest_face = original




def test_the_face_box_is_re_expressed_in_the_crops_own_fractions():
    box = (0.1, 0.05, 0.7, 0.8)
    fx, fy, fw, fh = crop.face_in_crop((0.4, 0.2, 0.2, 0.25), box)
    assert abs(fx * box[2] + box[0] - 0.4) < 1e-9 and abs(fw * box[2] - 0.2) < 1e-9 and 0 <= fx and fx + fw <= 1.0 and 0 <= fy and fy + fh <= 1.0








def test_only_flat_white_or_black_borders_are_trimmed_and_a_sky_or_a_busy_picture_is_not():
    left, top, w, h = crop.trim_box(picture(framed))
    assert left < 0.25 and top < 0.35 and 0.5 < w < 0.8 and h < 0.7 and left + w <= 1.0 and top + h <= 1.0
    black = picture(framed, ground=(0, 0, 0))
    assert crop.trim_box(black) is not None
    assert crop.trim_box(picture(lambda im: im.paste((120, 160, 220), (0, 0, 300, 400)))) is None
    assert crop.trim_box(picture(lambda im: im.paste((120, 160, 220), (0, 0, 300, 150)), ground=(90, 90, 90))) is None


def test_choose_locates_a_face_without_zooming_on_it_and_skips_the_search_when_not_asked():
    with faces(((0.4, 0.2, 0.2, 0.25), "yunet")):
        box, face_box, mode = crop.choose(picture(lambda im: im.paste((120, 120, 120), (0, 0, 300, 400))))
        assert box == crop.WHOLE and mode == "whole" and all(abs(a - b) < 1e-9 for a, b in zip(face_box, (0.4, 0.2, 0.2, 0.25)))
        assert crop.choose(picture(lambda im: None), False)[1] is None


def test_the_crop_is_cut_from_the_picture_and_described():
    from PIL import Image

    cut = Image.open(io.BytesIO(crop.crop_bytes(picture(lambda im: None), (0.25, 0.0, 0.5, 0.5))))
    assert cut.size == (150, 200) and crop.describe((0.25, 0.0, 0.5, 0.5)) == "0.25,0.00,0.50,0.50"


def test_no_face_is_found_in_a_blank_picture_or_in_bytes_that_are_not_a_picture():
    assert face.largest_face(picture(lambda im: None)) is None
    assert face.largest_face(b"not a picture") is None


def test_haar_takes_over_when_the_yunet_model_cannot_run_and_missing_opencv_means_no_face():
    original = (face._yunet, face._haar)
    face._yunet = lambda image: (_ for _ in ()).throw(RuntimeError("model missing"))
    face._haar = lambda image: [(10.0, 20.0, 30.0, 30.0)]
    try:
        found = face.largest_face(picture(lambda im: None, (100, 100)))
    finally:
        face._yunet, face._haar = original
    assert found is not None and found[1] == "haar" and found[0] == (0.1, 0.2, 0.3, 0.3)
    real_decode = face._decode
    face._decode = lambda data: (_ for _ in ()).throw(ImportError("no cv2"))
    try:
        assert face.largest_face(b"x") is None
    finally:
        face._decode = real_decode


def test_the_largest_of_several_faces_wins_and_the_model_file_ships_with_its_licence():
    original = face._yunet
    face._yunet = lambda image: [(5.0, 5.0, 10.0, 10.0), (50.0, 20.0, 40.0, 50.0)]
    try:
        found = face.largest_face(picture(lambda im: None, (100, 100)))
    finally:
        face._yunet = original
    assert found[0] == (0.5, 0.2, 0.4, 0.5)
    here = os.path.dirname(os.path.abspath(__file__))
    assert os.path.getsize(os.path.join(here, "face_detection_yunet_2023mar.onnx")) > 100_000
    assert "MIT License" in open(os.path.join(here, "LICENSE-yunet.txt")).read()


def rig_for_crop():
    rig = base.Rig()
    rig.png = picture(lambda im: None)
    base.sources.fetch_picture = lambda get, url: rig.png
    seen = []
    original = draw.photo.trace_photo
    draw.photo.trace_photo = lambda data, free, cap, face_box=None, shading=False, board="dark": seen.append((crop.size_of(data), face_box)) or original(data, free, cap, face_box, shading, board)
    rig.seen = seen
    return rig


def run_photo(rig, text="!draw photo a cat"):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rig.say(text)
    return [line for line in out.getvalue().splitlines() if line.startswith("draw ")][-1]








def test_photo_and_shaded_never_ask_the_model_and_spend_no_daily_call():
    class Planner:
        def __getattr__(self, name):
            raise AssertionError(f"the model must not be used: {name}")

    rig = rig_for_crop()
    draw.planner = Planner()
    with faces(None):
        run_photo(rig)
        run_photo(rig, "!draw shaded a cat")
    used = draw.bot.store.connection.execute("SELECT COALESCE(SUM(calls), 0) FROM ai_usage").fetchone()[0]
    assert used == 0


if __name__ == "__main__":
    run_module_tests(globals())
