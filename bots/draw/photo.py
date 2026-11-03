"""A line drawing traced from a photograph: edges, linked into polylines, simplified and ranked; Pillow only."""

import io
import math

from plan import Planned
from sources import SourceError

WORK_SIDE = 560  # pixels on the long side the photo is analysed at
KEEP_FRACTION = 0.16  # share of pixels that may be edges before thinning
MIN_STRENGTH = 14
LOCAL_RADIUS = 14  # blur radius of the neighbourhood an edge is judged against
LOCAL_GAIN = 1.25  # an edge must beat its neighbourhood's average strength by this factor
MIN_LENGTH = 10  # pixels; shorter fragments are noise
SIMPLIFY = 0.9  # Douglas-Peucker tolerance in pixels
SMOOTH = 2  # moving-average half window over a traced path
CHUNK_POINTS = 150
STROKE_WIDTH = 2.0
INK = "annotation"
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
            return gray.filter(ImageFilter.GaussianBlur(0.9))
    except Exception as err:
        raise SourceError("the picture could not be read") from err


def _gradients(gray):
    """(gx, gy) as signed lists from two Sobel passes."""
    from PIL import ImageFilter

    sobel_x = ImageFilter.Kernel((3, 3), (-1, 0, 1, -2, 0, 2, -1, 0, 1), scale=4, offset=128)
    sobel_y = ImageFilter.Kernel((3, 3), (-1, -2, -1, 0, 0, 0, 1, 2, 1), scale=4, offset=128)
    gx = [v - 128 for v in gray.filter(sobel_x).getdata()]
    gy = [v - 128 for v in gray.filter(sobel_y).getdata()]
    return gx, gy


def _local_mean(strength, width, height):
    """Each pixel's neighbourhood average of `strength`, so a faint edge in a quiet area still counts."""
    from PIL import Image, ImageFilter

    clipped = Image.new("L", (width, height))
    clipped.putdata([min(v, 255) for v in strength])
    return list(clipped.filter(ImageFilter.GaussianBlur(LOCAL_RADIUS)).getdata())


def thin_edges(gx, gy, width, height):
    """{(x, y): strength} of edge pixels one pixel thick: gradients well above their neighbourhood that are maxima across the edge."""
    strength = [abs(a) + abs(b) for a, b in zip(gx, gy)]
    around = _local_mean(strength, width, height)
    ordered = sorted(strength)
    floor = max(ordered[int(len(ordered) * (1 - KEEP_FRACTION))] * 0.4, MIN_STRENGTH)
    edges = {}
    for y in range(1, height - 1):
        for x in range(1, width - 1):
            index = y * width + x
            here = strength[index]
            if here < floor or here < around[index] * LOCAL_GAIN:
                continue
            step = 1 if abs(gx[index]) >= abs(gy[index]) else width
            if here >= strength[index - step] and here > strength[index + step]:
                edges[(x, y)] = here
    return edges


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


def _distance(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == dy == 0:
        return math.dist(point, start)
    return abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0]) / math.hypot(dx, dy)


def simplify(path, tolerance=SIMPLIFY):
    """Douglas-Peucker, iterative so a long path cannot overflow the stack."""
    keep = {0, len(path) - 1}
    todo = [(0, len(path) - 1)]
    while todo:
        first, last = todo.pop()
        far, at = 0.0, None
        for i in range(first + 1, last):
            d = _distance(path[i], path[first], path[last])
            if d > far:
                far, at = d, i
        if at is not None and far > tolerance:
            keep.add(at)
            todo += [(first, at), (at, last)]
    return [path[i] for i in sorted(keep)]


def _length(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def strokes(data, cap):
    """(polylines in photo pixels, width, height): the most salient edge lines, longest and strongest first, at most `cap` after splitting."""
    gray = _gray(data)
    width, height = gray.size
    gx, gy = _gradients(gray)
    edges = thin_edges(gx, gy, width, height)
    ranked = []
    for path in link(edges):
        if len(path) < MIN_LENGTH:
            continue
        line = simplify(_smooth(path))
        length = _length(line)
        if length >= MIN_LENGTH and len(line) >= 2:
            strength = sum(edges.get(p, 0) for p in path) / len(path)
            ranked.append((length * (0.5 + strength / 255) * _centre_bias(line, width, height), line))
    ranked.sort(key=lambda item: -item[0])
    chosen, used = [], 0
    for _, line in ranked:
        pieces = _split(line)
        if used + len(pieces) > cap:
            continue
        chosen.extend(pieces)
        used += len(pieces)
    return chosen, width, height


def _centre_bias(line, width, height):
    """1.0 for a line at the middle of the photo falling to 0.55 at a corner: the subject is usually central."""
    mx, my = sum(p[0] for p in line) / len(line), sum(p[1] for p in line) / len(line)
    off = math.hypot((mx - width / 2) / (width / 2), (my - height / 2) / (height / 2)) / math.sqrt(2)
    return 1.0 - 0.45 * off


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
