"""Turns a picture into a halftone dot portrait: a grid of tiny closed strokes whose size follows distance from the background; Pillow only."""

import io
import math

from plan import Planned
from sources import SourceError

BACKGROUND = 38  # cells this close (RGB distance) to the photo's border colour are background and get no dot
FULL_INK = 160  # distance from the background at which a dot reaches full size
MIN_DOT = 1.2  # smallest dot radius in canvas units
SIDES = 8  # a dot is a closed polygon this many sided
MAX_SIDE = 480  # pixels on the long side the picture is read at before gridding


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


def _border_colour(image):
    """The median colour of the photo's outer ring: what the background is."""
    w, h = image.size
    ring = [image.getpixel((x, y)) for x in range(w) for y in (0, h - 1)] + [image.getpixel((x, y)) for y in range(h) for x in (0, w - 1)]
    return tuple(sorted(p[channel] for p in ring)[len(ring) // 2] for channel in range(3))


def _cells(image, cols):
    """(columns, rows, [(ink 0..1, (r, g, b))...] row by row): the picture averaged into `cols` wide, ink = distance from the background."""
    from PIL import Image

    rows = max(round(cols * image.height / image.width), 1)
    small = image.resize((cols, rows), Image.BOX)
    background = _border_colour(image)
    cells = []
    for rgb in small.getdata():
        distance = math.dist(rgb, background)
        cells.append((0.0 if distance < BACKGROUND else min(distance / FULL_INK, 1.0), rgb))
    return cols, rows, cells


def _inked(cells):
    return sum(1 for ink, _ in cells if ink > 0)


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
    """Dots for the picture `data`, fitted inside `region` and centred, at most `cap` of them; a cell further from the background colour gets a bigger dot in its own colour."""
    image = _load(data)
    cols, rows, cells = _grid(image, cap)
    pitch = min(region.w / cols, region.h / rows)
    left, top = region.x + (region.w - pitch * cols) / 2, region.y + (region.h - pitch * rows) / 2
    max_radius = pitch * 0.5
    dots = []
    for index, (ink, rgb) in enumerate(cells):
        if ink <= 0:
            continue
        col, row = index % cols, index // cols
        radius = max(max_radius * math.sqrt(ink), MIN_DOT)
        dots.append(_dot(left + (col + 0.5) * pitch, top + (row + 0.5) * pitch, radius, hex_colour(rgb)))
    return dots
