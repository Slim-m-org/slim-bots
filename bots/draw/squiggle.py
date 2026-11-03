"""A tonal portrait drawn as wavy scanlines: each row's wave gets taller and tighter where the picture is darker; Pillow only."""

import io
import math

from plan import Planned
from sources import SourceError

WORK_SIDE = 560
CHUNK_POINTS = 150
STROKE_WIDTH = 1.6
INK = {"dark": "#f2f2f2", "light": "#1f1f1f"}  # a light ink on a dark board, a dark ink on a light one
ROWS_MIN, ROWS_MAX = 24, 100
BASE_FREQUENCY = 1.0  # radians per pixel in the lightest drawn tone
TONE_FREQUENCY = 1.0  # extra radians per pixel at the darkest
AMPLITUDE = 1.0  # of the row pitch at the darkest
TONE_GAMMA = 1.35  # above 1 keeps mid tones (skin) light so shadows and features stand out
JOIN_GAP = 5  # pixels of light a row may cross without lifting the pen
MIN_RUN = 6  # points: shorter runs are specks
LIGHT_SUBJECT = 14  # grey levels the middle must beat the ring by before a faceless picture is drawn light-on-dark
FLAT_RANGE = 12  # grey levels: a subject with less tone range than this is drawn as one even mid tone
FLAT_INK = 0.6
PEN_UP = 0.12  # darkness below which the pen lifts, so light areas stay empty paper
SIMPLIFY = 0.45
BACKGROUND = (28.0, 85.0)  # RGB distance from the border colour over which a pixel fades from paper to subject
PAPER_PERCENT = (0.03, 0.9)
LIGHT_INK_PERCENT = (0.25, 0.97)  # on a dark board the darkest quarter of the subject stays bare and the lightest 3 percent is full ink
HEAD_ZONE = 0.35  # face heights above the face top before only dark pixels (hair) count as subject  # tone percentiles that map to full ink and bare paper inside the subject


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


def _border_colour(rgb):
    w, h = rgb.size
    ring = [rgb.getpixel((x, y)) for x in range(w) for y in (0, h - 1)] + [rgb.getpixel((x, y)) for y in range(h) for x in (0, w - 1)]
    return tuple(sorted(p[c] for p in ring)[len(ring) // 2] for c in range(3))


def _ellipse(x, y, cx, cy, rx, ry):
    return 1.0 - _smoothstep(0.8, 1.2, math.hypot((x - cx) / rx, (y - cy) / ry))


def _head_and_shoulders(face, w, h, x, y):
    """1 inside an ellipse round the head or a wider, lower one for the shoulders, fading to 0 outside; `face` is (x, y, w, h) fractions."""
    fx, fy, fw, fh = face
    cx, cy = (fx + fw / 2) * w, (fy + fh / 2) * h
    head = _ellipse(x, y, cx, cy - 0.05 * fh * h, 0.62 * fw * w, 0.9 * fh * h)
    shoulders = _ellipse(x, y, cx, (fy + fh * 1.6) * h, 1.55 * fw * w, 0.9 * fh * h)
    return max(head, 0.8 * shoulders)


def _hair_limit(rgb, face):
    """The luminance below which a pixel beside or above the head can be hair: 0.8 of the face's own median."""
    w, h = rgb.size
    fx, fy, fw, fh = face
    px = rgb.load()
    samples = sorted(
        0.299 * px[x, y][0] + 0.587 * px[x, y][1] + 0.114 * px[x, y][2]
        for y in range(int(fy * h), int(min((fy + fh) * h, h)), 2) for x in range(int(fx * w), int(min((fx + fw) * w, w)), 2)
    )
    return 0.8 * samples[len(samples) // 2] if samples else 255.0


def _off_the_head(face, w, h, x, y):
    """True above or beside the head's core, where only hair-dark pixels belong to the subject."""
    fx, fy, fw, fh = face
    cx, cy = (fx + fw / 2) * w, (fy + fh / 2) * h
    core = _ellipse(x, y, cx, cy, 0.62 * fw * w, 0.72 * fh * h)
    return core < 0.95 and y < (fy + fh) * h or y < (fy - HEAD_ZONE * fh) * h


def subject_mask(rgb, face):
    """Per-pixel weights 0..1, row by row: 1 on the subject, 0 on background; round a face box an ellipse with the bright wall above and beside the head dropped, else distance from the border colour."""
    w, h = rgb.size
    border = _border_colour(rgb)
    px = rgb.load()
    limit = _hair_limit(rgb, face) if face else 0.0
    rows = []
    for y in range(h):
        row = []
        for x in range(w):
            if face:
                away = _head_and_shoulders(face, w, h, x, y)
                if away > 0 and _off_the_head(face, w, h, x, y):
                    lum = 0.299 * px[x, y][0] + 0.587 * px[x, y][1] + 0.114 * px[x, y][2]
                    away *= 1.0 - _smoothstep(0.9 * limit, 1.15 * limit, lum)
            else:
                away = _smoothstep(*BACKGROUND, math.dist(px[x, y], border))
                away *= 1.0 - 0.8 * _smoothstep(0.55, 1.15, math.hypot((x / w - 0.5) / 0.5, (y / h - 0.5) / 0.5))
            row.append(away)
        rows.append(row)
    return rows


def _subject_is_light(gray, board):
    """True when the subject should be inked on its light tones: the middle is clearly lighter than the ring, clearly darker means ink on its dark tones, else the board decides."""
    w, h = gray.size
    px = gray.load()
    middle, ring = [], []
    for y in range(0, h, 3):
        for x in range(0, w, 3):
            r = math.hypot((x / w - 0.5) / 0.5, (y / h - 0.5) / 0.5)
            (middle if r < 0.5 else ring if r > 0.85 else []).append(px[x, y])
    if not (middle and ring):
        return board == "dark"
    gap = sum(middle) / len(middle) - sum(ring) / len(ring)
    return gap > 0 if abs(gap) > LIGHT_SUBJECT else board == "dark"


def _scurve(t):
    return t * t * (3 - 2 * t)


def _ink_amount(gray, mask, ink_on_light):
    """Ink amount 0 (bare board) to 1 (full ink) over the subject's own tone range; with `ink_on_light` the darkest quarter stays bare and an S-curve and gamma separate mid tones."""
    w, h = gray.size
    px = gray.load()
    inside = sorted(px[x, y] for y in range(0, h, 2) for x in range(0, w, 2) if mask[y][x] > 0.5)
    if len(inside) < 50:
        return None
    if ink_on_light:
        lo, hi = inside[int(len(inside) * LIGHT_INK_PERCENT[0])], inside[min(int(len(inside) * LIGHT_INK_PERCENT[1]), len(inside) - 1)]
    else:
        lo, hi = inside[int(len(inside) * PAPER_PERCENT[0])], inside[int(len(inside) * PAPER_PERCENT[1])]
    span = max(hi - lo, 1)
    flat = hi - lo < FLAT_RANGE

    def tone(x, y):
        if flat:
            return FLAT_INK * mask[y][x]
        t = (px[x, y] - lo) / span if ink_on_light else (hi - px[x, y]) / span
        return _scurve(min(max(t, 0.0), 1.0)) ** TONE_GAMMA * mask[y][x]

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


def lines(data, cap, face=None, board="dark"):
    """(polylines in pixels, width, height): wavy rows over the subject, ink on light tones for a dark board and dark tones for a light one, as many rows as fit under `cap`."""
    rgb, gray = _load(data)
    w, h = rgb.size
    ink_on_light = (board == "dark") if face else _subject_is_light(gray, board)
    dark = _ink_amount(gray, subject_mask(rgb, face), ink_on_light)
    if dark is None:
        return [], w, h
    inked = [y for y in range(h) if max(dark[y]) >= PEN_UP]
    if not inked:
        return [], w, h
    top, bottom = inked[0], inked[-1]
    rows = ROWS_MAX
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


def squiggle(data, region, cap, face=None, board="dark"):
    """Stroke objects of the tonal squiggle portrait, fitted inside `region` with the picture's aspect ratio and centred."""
    pieces, w, h = lines(data, cap, face, board)
    scale = min(region.w / w, region.h / h)
    left, top = region.x + (region.w - scale * w) / 2, region.y + (region.h - scale * h) / 2
    return [_stroke(piece, scale, left, top, INK[board if board in INK else 'dark']) for piece in pieces]
