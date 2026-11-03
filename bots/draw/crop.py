"""Cutting flat white or black borders off a picture (fractions of it) and locating the largest face without cropping to it."""

import io

import face
from sources import SourceError

TRIM_SIDE = 160
EMPTY_RANGE = 10  # grey levels a line may vary by and still be flat
EMPTY_LIGHT, EMPTY_DARK = 238, 18  # only an almost white or black flat line is a border, a flat sky is picture
MIN_BORDER = 0.12  # of a side: smaller borders are left alone
TRIM_PAD = 0.02
WHOLE = (0.0, 0.0, 1.0, 1.0)


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


def face_in_crop(face_box, crop):
    """The face box (fractions of the picture) re-expressed as fractions of the crop, clamped inside it."""
    fx, fy, fw, fh = face_box
    cx, cy, cw, ch = crop
    x, y = (fx - cx) / cw, (fy - cy) / ch
    w, h = fw / cw, fh / ch
    left, top, right, bottom = max(x, 0.0), max(y, 0.0), min(x + w, 1.0), min(y + h, 1.0)
    return left, top, max(right - left, 0.01), max(bottom - top, 0.01)


def _margin(profile):
    """How many leading entries of `profile` (per-line (min, max, mean) of the grey picture) are a flat white or black border."""
    count = 0
    for low, high, mean in profile:
        if high - low >= EMPTY_RANGE or EMPTY_DARK < mean < EMPTY_LIGHT:
            break
        count += 1
    return count


def trim_box(data):
    """The box (fractions) left after cutting flat white or black borders that are at least MIN_BORDER of a side, or None when nothing is that empty."""
    with _open(data) as picture:
        gray = picture.convert("L")
        gray.thumbnail((TRIM_SIDE, TRIM_SIDE))
    w, h = gray.size
    px = gray.load()
    rows = [(min(v), max(v), sum(v) / w) for v in ([px[x, y] for x in range(w)] for y in range(h))]
    cols = [(min(v), max(v), sum(v) / h) for v in ([px[x, y] for y in range(h)] for x in range(w))]
    top, bottom = _margin(rows), _margin(rows[::-1])
    left, right = _margin(cols), _margin(cols[::-1])
    if top + bottom >= h or left + right >= w or max(top / h, bottom / h, left / w, right / w) < MIN_BORDER:
        return None
    x0, x1 = max(left / w - TRIM_PAD, 0.0), min(1.0 - right / w + TRIM_PAD, 1.0)
    y0, y1 = max(top / h - TRIM_PAD, 0.0), min(1.0 - bottom / h + TRIM_PAD, 1.0)
    if x1 - x0 < 0.2 or y1 - y0 < 0.2:
        return None
    return x0, y0, x1 - x0, y1 - y0


def choose(data, detect_face=True):
    """(crop box, face box in the crop or None, mode): only flat borders are cut, the face (when asked for) is located and never zoomed on."""
    try:
        box = trim_box(data)
    except SourceError:
        box = None
    box = box or WHOLE
    found = face.largest_face(data) if detect_face else None
    face_box = face_in_crop(found[0], box) if found else None
    return box, face_box, "trim" if box != WHOLE else "whole"
