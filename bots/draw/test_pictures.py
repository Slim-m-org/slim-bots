#!/usr/bin/env python3
"""Every place the draw bot decodes a picture refuses one that declares too many pixels, whichever code path ran first; run directly: python3 test_pictures.py."""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import colorize  # noqa: E402
import crop  # noqa: E402
import face  # noqa: E402
import paste  # noqa: E402
import photo  # noqa: E402
import pictures  # noqa: E402
import screenshot  # noqa: E402
import sizing  # noqa: E402
import sources  # noqa: E402
import squiggle  # noqa: E402
from PIL import Image  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

PILLOW_DEFAULT_LIMIT = 89_478_485


def png(side):
    out = io.BytesIO()
    Image.new("L", (side, side)).save(out, "PNG")
    return out.getvalue()


def refused(call, data):
    try:
        call(data)
    except sources.SourceError as err:
        return str(err) == "the picture could not be read"
    return False


def test_the_bound_is_set_when_the_helper_loads_not_when_some_other_step_ran():
    assert Image.MAX_IMAGE_PIXELS == pictures.MAX_PIXELS


def test_a_picture_past_the_bound_is_refused_from_its_header():
    Image.MAX_IMAGE_PIXELS = PILLOW_DEFAULT_LIMIT
    side = int(pictures.MAX_PIXELS**0.5) + 100
    try:
        pictures.open_picture(png(side))
    except pictures.PictureTooLarge:
        pass
    else:
        raise AssertionError("expected PictureTooLarge")
    finally:
        Image.MAX_IMAGE_PIXELS = pictures.MAX_PIXELS
    with pictures.open_picture(png(64)) as small:
        assert small.size == (64, 64)


def test_every_decode_step_refuses_a_huge_picture_even_in_a_process_that_never_lowered_the_limit():
    huge = png(int(pictures.MAX_PIXELS**0.5) + 100)
    steps = {
        "decode_gray": lambda data: sources.decode_gray(data, 32),
        "reference_image": sources.reference_image,
        "size_of": crop.size_of,
        "crop_bytes": lambda data: crop.crop_bytes(data, (0.0, 0.0, 0.5, 0.5)),
        "colorize": colorize._load,
        "measure": sizing.measure,
        "prepare": paste.prepare,
        "squiggle": squiggle._load,
        "photo": photo._gray,
    }
    Image.MAX_IMAGE_PIXELS = PILLOW_DEFAULT_LIMIT
    try:
        not_refused = [name for name, step in steps.items() if not refused(step, huge)]
    finally:
        Image.MAX_IMAGE_PIXELS = pictures.MAX_PIXELS
    assert not not_refused, f"decoded a picture past the pixel bound: {not_refused}"


def test_opencv_is_never_handed_a_picture_past_the_bound_but_still_reads_one_inside_it():
    import cv2

    handed = []
    real = cv2.imdecode
    cv2.imdecode = lambda *args, **kwargs: handed.append(1) or real(*args, **kwargs)
    try:
        huge = png(int(pictures.MAX_PIXELS**0.5) + 100)
        assert face.largest_face(huge) is None
        assert crop.choose(huge) == (crop.WHOLE, None, "whole")
        assert handed == [], "OpenCV was asked to decode a picture past the pixel bound"
        face.largest_face(png(64))
        assert handed == [1]
    finally:
        cv2.imdecode = real


def test_a_huge_canvas_tile_is_left_as_a_grey_box_instead_of_decoded():
    huge = png(int(pictures.MAX_PIXELS**0.5) + 100)
    view = screenshot.View(screenshot.Region(0, 0, 400, 300))
    image = Image.new("RGB", view.size, screenshot.PAPER)
    obj = {"x": 0, "y": 0, "w": 100, "h": 100}
    Image.MAX_IMAGE_PIXELS = PILLOW_DEFAULT_LIMIT
    try:
        assert screenshot._paste_tile(image, view, obj, huge) is False
    finally:
        Image.MAX_IMAGE_PIXELS = pictures.MAX_PIXELS
    assert screenshot._paste_tile(image, view, obj, png(32)) is True


if __name__ == "__main__":
    run_module_tests(globals())
