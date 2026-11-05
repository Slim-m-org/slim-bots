"""A whole picture as coloured brushwork: rows of horizontal strokes that touch, each a run of one palette colour; Pillow only."""

import io
import math

import sizing
from plan import Planned
from sources import SourceError

PALETTE = 48  # colours the picture is reduced to before runs are merged
MIN_ROWS, MAX_ROWS = 24, 220
WIDTH_FACTOR = 1.3  # stroke width over row pitch: rows overlap a little so no board shows between round caps
OVERLAP = 0.15  # pitches a run reaches past its own cells at each end
WORK_SIDE = 640  # pixels on the long side the picture is read at


def hex_colour(rgb):
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _load(data):
    try:
        from PIL import Image
    except ImportError as err:
        raise SourceError("Pillow is not installed on this bot, so it cannot read pictures") from err
    try:
        with Image.open(io.BytesIO(data)) as picture:
            picture.draft("RGB", (WORK_SIDE * 2, WORK_SIDE * 2))
            rgb = picture.convert("RGB")
            rgb.thumbnail((WORK_SIDE, WORK_SIDE))
            return rgb
    except Exception as err:
        raise SourceError("the picture could not be read") from err


def _reduced(image, rows, cols):
    """The picture as `cols` by `rows` cells, each one of PALETTE colours: (palette as rgb tuples, rows of palette indexes)."""
    from PIL import Image, ImageFilter

    small = image.resize((cols, rows), Image.BOX).filter(ImageFilter.MedianFilter(3))
    quantised = small.quantize(colors=PALETTE, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    flat = list(quantised.getdata())
    raw = quantised.getpalette() or []
    palette = [tuple(raw[i : i + 3]) for i in range(0, len(raw), 3)]
    return palette, [flat[y * cols : (y + 1) * cols] for y in range(rows)]


def _runs(grid):
    """(row, first cell, last cell, palette index) for every maximal run of equal cells along each row."""
    out = []
    for y, row in enumerate(grid):
        start = 0
        for x in range(1, len(row) + 1):
            if x == len(row) or row[x] != row[start]:
                out.append((y, start, x - 1, row[start]))
                start = x
    return out


def _grid_for(image, cap, start_rows):
    """The finest (rows, cols, palette, runs) whose strokes fit under `cap`: the row count is stepped down until they do."""
    aspect = image.width / image.height
    rows = start_rows
    while True:
        cols = max(int(rows * aspect), 1)
        palette, grid = _reduced(image, rows, cols)
        runs = _runs(grid)
        if len(runs) <= cap or rows <= MIN_ROWS:
            return rows, cols, palette, runs[:cap]
        rows = max(int(rows * min(0.92, math.sqrt(cap / len(runs)) * 1.02)), MIN_ROWS)


def _stroke(run, palette, region_x, region_y, pitch, scale_x):
    y, first, last, index = run
    width = pitch * WIDTH_FACTOR
    pad = width / 2 - OVERLAP * pitch
    x0 = region_x + first * scale_x + pad
    x1 = region_x + (last + 1) * scale_x - pad
    cy = region_y + (y + 0.5) * pitch
    if x1 < x0:
        x0 = x1 = (x0 + x1) / 2
    return Planned(
        "stroke", x0, cy, max(x1 - x0, 0.5), 0.5, {"points": [0.0, 0.0, round(max(x1 - x0, 0.5), 1), 0.0], "width": round(width, 1), "color": hex_colour(palette[index])},
    )


def color_strokes(data, region, cap):
    """Stroke objects painting the whole picture inside `region` (aspect kept, centred), as many rows as fit under `cap` strokes."""
    image = _load(data)
    detail = sizing.measure(data)
    rows, cols, palette, runs = _grid_for(image, cap, sizing.rows_for(detail, MIN_ROWS * 2, MAX_ROWS))
    left, top, box_w, _ = sizing.drawing_box(region, cols / rows, detail)
    pitch = box_w / cols
    return [_stroke(run, palette, left, top, pitch, pitch) for run in runs]
