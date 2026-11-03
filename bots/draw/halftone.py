"""Turns a picture into a halftone dot portrait: a grid of tiny closed strokes whose size follows darkness; Pillow only."""

import io
import math

from plan import Planned
from sources import SourceError

BLANK = 0.12  # cells lighter than this are the background and get no dot
MIN_DOT = 1.2  # smallest dot radius in canvas units
SIDES = 8  # a dot is a closed polygon this many sided
MAX_SIDE = 400  # pixels on the long side the picture is read at before gridding


def _load(data):
    try:
        from PIL import Image
    except ImportError as err:
        raise SourceError("Pillow is not installed on this bot, so it cannot read pictures") from err
    try:
        with Image.open(io.BytesIO(data)) as picture:
            picture.draft("RGB", (MAX_SIDE * 2, MAX_SIDE * 2))
            rgb = picture.convert("RGB")
            rgb.thumbnail((MAX_SIDE, MAX_SIDE))
            return rgb
    except Exception as err:
        raise SourceError("the picture could not be read") from err


def _cells(image, cols):
    """(columns, rows, [(darkness 0..1, (r, g, b))...] row by row) averaging the picture into a `cols`-wide grid."""
    from PIL import Image

    rows = max(round(cols * image.height / image.width), 1)
    small = image.resize((cols, rows), Image.BOX)
    pixels = list(small.getdata())
    cells = [(1 - (0.299 * r + 0.587 * g + 0.114 * b) / 255, (r, g, b)) for r, g, b in pixels]
    return cols, rows, cells


def _inked(cells):
    return sum(1 for darkness, _ in cells if darkness >= BLANK)


def _grid(image, cap):
    """The finest grid whose inked cells fit under `cap`, found by narrowing the columns until they do."""
    cols = min(MAX_SIDE, max(int(math.sqrt(cap * 2.2 * image.width / image.height)), 8))
    while True:
        grid = _cells(image, cols)
        if _inked(grid[2]) <= cap or cols <= 8:
            return grid
        cols -= max(cols // 20, 1)


def hex_colour(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _dot(cx, cy, radius, colour):
    ring = [(cx + radius * 0.5 * math.cos(2 * math.pi * i / SIDES), cy + radius * 0.5 * math.sin(2 * math.pi * i / SIDES)) for i in range(SIDES + 1)]
    low_x, low_y = min(p[0] for p in ring), min(p[1] for p in ring)
    flat = [round(v, 1) for p in ring for v in (p[0] - low_x, p[1] - low_y)]
    width = max(radius, MIN_DOT)
    side = max(max(p[0] for p in ring) - low_x, 0.5)
    return Planned("stroke", low_x, low_y, side, side, {"points": flat, "width": round(width, 1), "color": colour})


def halftone(data, region, cap):
    """Dots for the picture `data`, fitted inside `region` and centred, at most `cap` of them, darker cells with bigger dots."""
    image = _load(data)
    cols, rows, cells = _grid(image, cap)
    pitch = min(region.w / cols, region.h / rows)
    left, top = region.x + (region.w - pitch * cols) / 2, region.y + (region.h - pitch * rows) / 2
    max_radius = pitch * 0.5
    dots = []
    for index, (darkness, rgb) in enumerate(cells):
        if darkness < BLANK:
            continue
        col, row = index % cols, index // cols
        radius = max(max_radius * math.sqrt(darkness), MIN_DOT)
        dots.append(_dot(left + (col + 0.5) * pitch, top + (row + 0.5) * pitch, radius, hex_colour(rgb)))
    return dots
