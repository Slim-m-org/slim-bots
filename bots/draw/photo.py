"""A line drawing traced from a photograph: strong connected edges, linked, smoothed, filtered and ranked; Pillow only."""

import io
import math

import photo_quality as quality
from photo_quality import drop_isolated, hugs_border  # noqa: F401  (re-exported for callers and tests)
from plan import Planned
from sources import SourceError

WORK_SIDE = 560  # pixels on the long side the photo is analysed at: enough for structure, too little for texture
BLUR = 1.5
HIGH_PERCENT = 0.85  # an edge pixel is strong when its gradient is above this share of the picture's
LOW_RATIO = 0.3  # weaker edge pixels survive only when they touch a strong one, at this fraction of the strong cut-off
LOCAL_RADIUS = 20  # blur radius of the neighbourhood an edge is judged against
LOCAL_GAIN = 1.1  # an edge must beat its neighbourhood's average strength by this factor
MIN_STRENGTH = 14
SIMPLIFY = 1.3  # Douglas-Peucker tolerance in pixels
SMOOTH = 4  # moving-average half window over a traced path, before simplifying
CHUNK_POINTS = 150
CENTRE_WEIGHT = 0.15
STROKE_WIDTH = 2.0
INK = "annotation"
FACE_SENSITIVITY = 0.55  # inside the face ellipse an edge needs only this share of the usual strength: eyes, nose and lips are faint
FACE_MIN_FRACTION = 0.02  # of the diagonal: the shortest line worth keeping there
NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, 1), (1, -1), (-1, -1))


def _gray(data):
    try:
        from PIL import Image, ImageFilter, ImageOps
    except ImportError as err:
        raise SourceError("Pillow is not installed on this bot, so it cannot read pictures") from err
    try:
        with Image.open(io.BytesIO(data)) as picture:
            picture.draft("L", (WORK_SIDE * 2, WORK_SIDE * 2))
            gray = ImageOps.autocontrast(picture.convert("L"), cutoff=1)
            gray.thumbnail((WORK_SIDE, WORK_SIDE))
            return gray.filter(ImageFilter.GaussianBlur(BLUR))
    except Exception as err:
        raise SourceError("the picture could not be read") from err


def _gradients(gray):
    """(gx, gy) as signed lists from two Sobel passes."""
    from PIL import ImageFilter

    sobel_x = ImageFilter.Kernel((3, 3), (-1, 0, 1, -2, 0, 2, -1, 0, 1), scale=4, offset=128)
    sobel_y = ImageFilter.Kernel((3, 3), (-1, -2, -1, 0, 0, 0, 1, 2, 1), scale=4, offset=128)
    return [v - 128 for v in gray.filter(sobel_x).getdata()], [v - 128 for v in gray.filter(sobel_y).getdata()]


def _local_mean(strength, width, height):
    """Each pixel's neighbourhood average of `strength`, so texture (busy everywhere) does not count as an edge."""
    from PIL import Image, ImageFilter

    clipped = Image.new("L", (width, height))
    clipped.putdata([min(v, 255) for v in strength])
    return list(clipped.filter(ImageFilter.GaussianBlur(LOCAL_RADIUS)).getdata())


def thin_edges(gx, gy, width, height):
    """({(x, y): strength}, strong cut-off): edge pixels one pixel thick, kept by hysteresis so every survivor is connected to a strong one."""
    strength = [abs(a) + abs(b) for a, b in zip(gx, gy)]
    around = _local_mean(strength, width, height)
    high = max(sorted(strength)[int(len(strength) * HIGH_PERCENT)], MIN_STRENGTH)
    low = high * LOW_RATIO
    weak = {}
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            index = y * width + x
            here = strength[index]
            ease = FACE_SENSITIVITY if quality.on_face((x, y), width, height) else 1.0
            if here < low * ease or here < around[index] * LOCAL_GAIN * ease:
                continue
            step = 1 if abs(gx[index]) >= abs(gy[index]) else width
            if here >= strength[index - step] and here > strength[index + step]:
                weak[(x, y)] = here
    return _hysteresis(weak, high, width, height), high


def _hysteresis(weak, high, width, height):
    """The weak edge pixels connected (8 ways) to at least one pixel at or above `high`."""
    kept = {p for p, v in weak.items() if v >= high}
    kept |= {p for p, v in weak.items() if v >= high * FACE_SENSITIVITY and quality.on_face(p, width, height)}
    stack = list(kept)
    while stack:
        x, y = stack.pop()
        for dx, dy in NEIGHBOURS:
            q = (x + dx, y + dy)
            if q in weak and q not in kept:
                kept.add(q)
                stack.append(q)
    return {p: weak[p] for p in kept}


def _degree(point, free):
    return sum(1 for dx, dy in NEIGHBOURS if (point[0] + dx, point[1] + dy) in free)


def _walk(start, free):
    """Follows unvisited edge pixels from `start`, keeping its heading where it can, removing them from `free`."""
    path, heading, current = [start], None, start
    free.discard(start)
    while True:
        options = [(dx, dy) for dx, dy in NEIGHBOURS if (current[0] + dx, current[1] + dy) in free]
        if not options:
            return path
        step = min(options, key=lambda d: (0 if heading is None else abs(d[0] - heading[0]) + abs(d[1] - heading[1]), abs(d[0]) + abs(d[1])))
        current = (current[0] + step[0], current[1] + step[1])
        free.discard(current)
        path.append(current)
        heading = step


def link(edges):
    """Polylines (lists of (x, y)) covering the edge pixels, started at line ends first."""
    free = set(edges)
    paths = []
    for point in sorted(edges, key=lambda p: (_degree(p, free), p)):
        if point in free:
            paths.append(_walk(point, free))
    return paths


def _smooth(path):
    if len(path) < 2 * SMOOTH + 1:
        return path
    out = [path[0]]
    for i in range(1, len(path) - 1):
        lo, hi = max(i - SMOOTH, 0), min(i + SMOOTH, len(path) - 1)
        span = path[lo : hi + 1]
        out.append((sum(p[0] for p in span) / len(span), sum(p[1] for p in span) / len(span)))
    return out + [path[-1]]


def simplify(path, tolerance=SIMPLIFY):
    """Douglas-Peucker, iterative so a long path cannot overflow the stack."""
    keep = {0, len(path) - 1}
    todo = [(0, len(path) - 1)]
    while todo:
        first, last = todo.pop()
        far, at = 0.0, None
        for i in range(first + 1, last):
            d = quality.distance(path[i], path[first], path[last])
            if d > far:
                far, at = d, i
        if at is not None and far > tolerance:
            keep.add(at)
            todo += [(first, at), (at, last)]
    return [path[i] for i in sorted(keep)]


def _worth_drawing(path, line, width, height):
    """The quality bar every line must pass: long enough, not jagged, not a border, curtain or frame, and on the subject."""
    diagonal = math.hypot(width, height)
    return (
        len(line) >= 2
        and quality.length(line) >= (FACE_MIN_FRACTION if quality.on_face(line[len(line) // 2], width, height) else quality.MIN_FRACTION) * diagonal
        and not quality.wiggly(path, diagonal)
        and not hugs_border(line, width, height)
        and not quality.background_straight(line, width, height)
        and quality.subject_distance(line, width, height) <= quality.SUBJECT_REACH
    )


def strokes(data, cap):
    """(polylines in photo pixels, width, height): the longest, strongest lines on the subject, at most `cap` after splitting."""
    gray = _gray(data)
    width, height = gray.size
    gx, gy = _gradients(gray)
    edges, _ = thin_edges(gx, gy, width, height)
    traced = []
    for path in link(edges):
        smooth = _smooth(path)
        line = simplify(smooth)
        if _worth_drawing(smooth, line, width, height):
            traced.append((quality.chaikin(line), sum(edges.get(p, 0) for p in path) / len(path)))
    survivors = {id(line) for line in drop_isolated([line for line, _ in traced])}
    ranked = sorted(
        ((quality.length(line) * (0.5 + strength / 255) * quality.subject_weight(line, width, height), line) for line, strength in traced if id(line) in survivors),
        key=lambda item: -item[0],
    )
    chosen, used = [], 0
    for _, line in ranked:
        pieces = _split(line)
        if used + len(pieces) <= cap:
            chosen.extend(pieces)
            used += len(pieces)
    return chosen, width, height


def _split(line):
    """A line cut into pieces of at most CHUNK_POINTS points that share their joints."""
    pieces, start = [], 0
    while True:
        pieces.append(line[start : start + CHUNK_POINTS])
        start += CHUNK_POINTS - 1
        if start >= len(line) - 1:
            return pieces


def _stroke(points, scale, left, top):
    scaled = [(left + x * scale, top + y * scale) for x, y in points]
    low_x, low_y = min(p[0] for p in scaled), min(p[1] for p in scaled)
    flat = [round(v, 1) for p in scaled for v in (p[0] - low_x, p[1] - low_y)]
    return Planned(
        "stroke", low_x, low_y, max(max(p[0] for p in scaled) - low_x, 0.5), max(max(p[1] for p in scaled) - low_y, 0.5),
        {"points": flat, "width": STROKE_WIDTH, "color": INK},
    )


def trace_photo(data, region, cap):
    """Stroke objects of the photo's edge lines, fitted inside `region` with the photo's aspect ratio and centred."""
    lines, width, height = strokes(data, cap)
    scale = min(region.w / width, region.h / height)
    left, top = region.x + (region.w - scale * width) / 2, region.y + (region.h - scale * height) / 2
    return [_stroke(line, scale, left, top) for line in lines]
