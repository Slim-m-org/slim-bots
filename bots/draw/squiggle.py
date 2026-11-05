"""A tonal picture drawn as wavy scanlines: each row's wave gets taller and tighter where the picture is darker; Pillow only."""

import io
import math

import sizing
from plan import Planned
from sources import SourceError

WORK_SIDE = 900
CHUNK_POINTS = 150
STROKE_WIDTH = 1.6
INK = {"dark": "#f2f2f2", "light": "#1f1f1f"}  # a light ink on a dark board, a dark ink on a light one
ROWS_MIN, ROWS_MAX = 40, 200  # a simple picture starts at the fewest rows and a busy one at the most
BASE_FREQUENCY = 1.0  # radians per pixel in the lightest drawn tone
TONE_FREQUENCY = 1.0  # extra radians per pixel at the darkest
AMPLITUDE = 1.0  # of the row pitch at the darkest
TONE_GAMMA = 1.35  # above 1 keeps mid tones (skin) light so shadows and features stand out
JOIN_GAP = 5  # pixels of light a row may cross without lifting the pen
MIN_RUN = 6  # points: shorter runs are specks
FLAT_RANGE = 12  # grey levels: a picture with less tone range than this has nothing to draw
PEN_UP = 0.12  # darkness below which the pen lifts, so light areas stay empty paper
SIMPLIFY = 0.45
PAPER_PERCENT = (0.03, 0.9)
LIGHT_INK_PERCENT = (0.25, 0.97)  # on a dark board the darkest quarter of the subject stays bare and the lightest 3 percent is full ink


def _smoothstep(low, high, value):
    t = min(max((value - low) / (high - low), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def _load(data):
    try:
        from PIL import Image, ImageFilter
    except ImportError as err:
        raise SourceError("Pillow is not installed on this bot, so it cannot read pictures") from err
    try:
        with Image.open(io.BytesIO(data)) as picture:
            picture.draft("RGB", (WORK_SIDE * 2, WORK_SIDE * 2))
            rgb = picture.convert("RGB")
            rgb.thumbnail((WORK_SIDE, WORK_SIDE))
    except Exception as err:
        raise SourceError("the picture could not be read") from err
    return rgb, rgb.convert("L").filter(ImageFilter.GaussianBlur(1.0))


def _scurve(t):
    return t * t * (3 - 2 * t)


def _ink_amount(gray, ink_on_light):
    """Ink amount 0 (bare board) to 1 (full ink) over the picture's tone range; with `ink_on_light` the darkest quarter stays bare and an S-curve and gamma separate mid tones."""
    w, h = gray.size
    px = gray.load()
    inside = sorted(px[x, y] for y in range(0, h, 2) for x in range(0, w, 2))
    if len(inside) < 50:
        return None
    if ink_on_light:
        lo, hi = inside[int(len(inside) * LIGHT_INK_PERCENT[0])], inside[min(int(len(inside) * LIGHT_INK_PERCENT[1]), len(inside) - 1)]
    else:
        lo, hi = inside[int(len(inside) * PAPER_PERCENT[0])], inside[int(len(inside) * PAPER_PERCENT[1])]
    span = max(hi - lo, 1)
    if hi - lo < FLAT_RANGE:
        return None

    def tone(x, y):
        t = (px[x, y] - lo) / span if ink_on_light else (hi - px[x, y]) / span
        return _scurve(min(max(t, 0.0), 1.0)) ** TONE_GAMMA

    return [[tone(x, y) for x in range(w)] for y in range(h)]


def _row(dark, w, y, pitch):
    """The runs (polylines in pixels) of one wavy row: the wave grows and tightens with darkness and the pen lifts where it is light; short gaps are bridged."""
    runs, run, phase, last = [], [], 0.0, -1000
    h = len(dark)
    ys = [min(max(int(y + k * pitch / 3), 0), h - 1) for k in (-1, 0, 1)]
    for x in range(w):
        d = sum(dark[yy][x] for yy in ys) / 3
        phase += BASE_FREQUENCY + TONE_FREQUENCY * d
        if d < PEN_UP:
            continue
        if not run and runs and x - last <= JOIN_GAP:
            run = runs.pop() + [(float(x - 1), y)]
        elif run and x - last > JOIN_GAP:
            runs.append(run)
            run = []
        run.append((float(x), min(max(y + AMPLITUDE * pitch * d ** 0.85 * math.sin(phase), 0.0), h - 1.0)))
        last = x
    if run:
        runs.append(run)
    return [r for r in runs if len(r) >= MIN_RUN]


def _simplify(path):
    from photo import simplify

    return simplify(path, SIMPLIFY)


def _pieces(runs):
    out = []
    for run in runs:
        line = _simplify(run)
        start = 0
        while True:
            out.append(line[start : start + CHUNK_POINTS])
            start += CHUNK_POINTS - 1
            if start >= len(line) - 1:
                break
    return [p for p in out if len(p) >= 2]


def lines(data, cap, board="dark", start_rows=ROWS_MAX):
    """(polylines in pixels, width, height): wavy rows over the whole picture, ink on light tones for a dark board and dark tones for a light one, from `start_rows` rows down to as many as fit under `cap`."""
    rgb, gray = _load(data)
    w, h = rgb.size
    dark = _ink_amount(gray, board == "dark")
    if dark is None:
        return [], w, h
    inked = [y for y in range(h) if max(dark[y]) >= PEN_UP]
    if not inked:
        return [], w, h
    top, bottom = inked[0], inked[-1]
    rows = start_rows
    while True:
        pitch = max((bottom - top) / rows, 1.5)
        pieces = _pieces([run for r in range(rows) for run in _row(dark, w, top + pitch * (r + 0.5), pitch)])
        if len(pieces) <= cap or rows <= ROWS_MIN:
            return pieces[:cap], w, h
        rows = max(int(rows * min(0.9, cap / len(pieces) * 1.05)), ROWS_MIN)


def _stroke(points, scale, left, top, ink):
    scaled = [(left + x * scale, top + y * scale) for x, y in points]
    low_x, low_y = min(p[0] for p in scaled), min(p[1] for p in scaled)
    flat = [round(v, 1) for p in scaled for v in (p[0] - low_x, p[1] - low_y)]
    return Planned("stroke", low_x, low_y, max(max(p[0] for p in scaled) - low_x, 0.5), max(max(p[1] for p in scaled) - low_y, 0.5), {"points": flat, "width": STROKE_WIDTH, "color": ink})


def squiggle(data, region, cap, board="dark"):
    """Stroke objects of the tonal squiggle picture, sized by its detail inside `region` with the picture's aspect ratio and centred."""
    detail = sizing.measure(data)
    pieces, w, h = lines(data, cap, board, sizing.rows_for(detail, ROWS_MIN, ROWS_MAX))
    x, y, box_w, _ = sizing.drawing_box(region, w / h, detail)
    scale = box_w / w
    return [_stroke(piece, scale, x, y, INK[board if board in INK else "dark"]) for piece in pieces]
