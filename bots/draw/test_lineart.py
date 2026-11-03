#!/usr/bin/env python3
"""Face-aware tracing and the optional pen shading; run directly: python3 test_lineart.py."""

import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import photo  # noqa: E402
import photo_quality as quality  # noqa: E402
import photo_shade  # noqa: E402
import plan  # noqa: E402
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


def test_a_face_box_with_shading_on_adds_hatching_and_respects_the_cap():
    plain = photo.trace_photo(portrait(), REGION, 300)
    shaded = photo.trace_photo(portrait(), REGION, 300, FACE, True)
    assert plain and len(shaded) > len(plain)
    assert all(len(__import__("json").dumps(o.props)) < plan.PROPS_LIMIT and len(o.props["points"]) // 2 <= photo.CHUNK_POINTS for o in shaded)
    assert len(photo.trace_photo(portrait(), REGION, 40, FACE, True)) <= 40


def test_hatching_is_off_by_default_and_the_flag_adds_it():
    plain = photo.trace_photo(portrait(), REGION, 300, FACE)
    shaded = photo.trace_photo(portrait(), REGION, 300, FACE, True)
    assert plain and len(shaded) > len(plain)
    assert len(photo.trace_photo(portrait(), REGION, 300)) == len(photo.trace_photo(portrait(), REGION, 300, None, False))


if __name__ == "__main__":
    run_module_tests(globals())
