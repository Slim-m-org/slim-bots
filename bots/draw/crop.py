"""Choosing and applying a crop box (fractions of the picture) so a trace focuses on the subject; Pillow only."""

import io
import math

from sources import SourceError

PAD = 0.08  # of the box's own size, each side
MIN_SIDE = 0.2  # no box is thinner than this fraction of the picture
PREVIEW_SIDE = 512
PERSON_ASPECT = 1.1  # height over width from which a picture is treated as a portrait


def clamp_box(raw):
    """A padded, clamped (x, y, w, h) in fractions from a model's answer, or None when it is not a usable box."""
    try:
        x, y, w, h = (float(raw[key]) for key in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (x, y, w, h)) or w <= 0 or h <= 0:
        return None
    x, y = min(max(x, 0.0), 1.0 - MIN_SIDE), min(max(y, 0.0), 1.0 - MIN_SIDE)
    w, h = min(max(w, MIN_SIDE), 1.0 - x), min(max(h, MIN_SIDE), 1.0 - y)
    left, top = max(x - PAD * w, 0.0), max(y - PAD * h, 0.0)
    right, bottom = min(x + w + PAD * w, 1.0), min(y + h + PAD * h, 1.0)
    return left, top, right - left, bottom - top


def heuristic_box(width, height):
    """The centre-top 70% of a portrait-shaped picture, the whole picture otherwise."""
    if height / max(width, 1) >= PERSON_ASPECT:
        return 0.15, 0.0, 0.7, 0.7
    return 0.0, 0.0, 1.0, 1.0


def _open(data):
    try:
        from PIL import Image
    except ImportError as err:
        raise SourceError("Pillow is not installed on this bot, so it cannot read pictures") from err
    try:
        return Image.open(io.BytesIO(data))
    except Exception as err:
        raise SourceError("the picture could not be read") from err


def size_of(data):
    with _open(data) as picture:
        return picture.size


def preview(data, side=PREVIEW_SIDE):
    """(JPEG bytes, media type) of the picture shrunk to `side` pixels, for a model to look at."""
    with _open(data) as picture:
        rgb = picture.convert("RGB")
        rgb.thumbnail((side, side))
        out = io.BytesIO()
        rgb.save(out, "JPEG", quality=80)
    return out.getvalue(), "image/jpeg"


def crop_bytes(data, box):
    """The picture cut to `box` (fractions), as PNG bytes; the whole picture when the box is all of it."""
    x, y, w, h = box
    with _open(data) as picture:
        rgb = picture.convert("RGB")
        width, height = rgb.size
        window = (round(x * width), round(y * height), round((x + w) * width), round((y + h) * height))
        out = io.BytesIO()
        rgb.crop(window).save(out, "PNG")
    return out.getvalue()


def describe(box):
    return ",".join(f"{v:.2f}" for v in box)
