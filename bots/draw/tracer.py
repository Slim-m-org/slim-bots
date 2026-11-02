"""Turns a small grayscale picture into a bounded set of polylines: posterise, find contours, simplify, cut to a budget."""

from collections import defaultdict

LEVELS = 3
SIMPLIFY_STEPS = (0.6, 0.9, 1.3, 1.9, 2.8, 4.0, 6.0)
MIN_LENGTH = 6.0
MAX_POINTS = 120

# Marching-squares segments per corner pattern (a=top-left b=top-right c=bottom-right d=bottom-left), as edge-midpoint pairs.
SEGMENTS = {
    1: (("L", "B"),), 2: (("B", "R"),), 3: (("L", "R"),), 4: (("T", "R"),), 5: (("T", "L"), ("B", "R")), 6: (("T", "B"),),
    7: (("T", "L"),), 8: (("T", "L"),), 9: (("T", "B"),), 10: (("T", "R"), ("L", "B")), 11: (("T", "R"),), 12: (("L", "R"),),
    13: (("B", "R"),), 14: (("L", "B"),),
}


def blur(gray):
    """A 3x3 box blur that keeps the picture's size; it takes the speckle out before the levels are cut."""
    h, w = len(gray), len(gray[0])
    out = []
    for y in range(h):
        row = []
        for x in range(w):
            cells = [gray[j][i] for j in range(max(y - 1, 0), min(y + 2, h)) for i in range(max(x - 1, 0), min(x + 2, w))]
            row.append(sum(cells) / len(cells))
        out.append(row)
    return out


def thresholds(gray, levels=LEVELS):
    """Brightness cuts at even quantiles of the picture, so a flat-toned photo still splits into bands."""
    values = sorted(v for row in gray for v in row)
    cuts = [values[len(values) * (i + 1) // (levels + 1)] for i in range(levels)]
    return sorted(set(cuts))


def contours(gray, cut):
    """Closed outlines of the area darker than `cut`, as lists of (x, y) in pixel units."""
    h, w = len(gray), len(gray[0])
    dark = [[False] * (w + 2) for _ in range(h + 2)]
    for y in range(h):
        for x in range(w):
            dark[y + 1][x + 1] = gray[y][x] < cut
    segments = []
    for y in range(h + 1):
        for x in range(w + 1):
            code = dark[y][x] * 8 + dark[y][x + 1] * 4 + dark[y + 1][x + 1] * 2 + dark[y + 1][x]
            mids = {"T": (2 * x + 1, 2 * y), "R": (2 * x + 2, 2 * y + 1), "B": (2 * x + 1, 2 * y + 2), "L": (2 * x, 2 * y + 1)}
            for start, end in SEGMENTS.get(code, ()):
                segments.append((mids[start], mids[end]))
    return [[((px - 2) / 2, (py - 2) / 2) for px, py in path] for path in _link(segments)]


def _link(segments):
    neighbours = defaultdict(list)
    for p, q in segments:
        neighbours[p].append(q)
        neighbours[q].append(p)
    used, paths = set(), []
    for p, q in segments:
        if (p, q) in used or (q, p) in used:
            continue
        used.add((p, q))
        path = [p, q]
        while True:
            here = path[-1]
            step = next((n for n in neighbours[here] if (here, n) not in used and (n, here) not in used), None)
            if step is None:
                break
            used.add((here, step))
            path.append(step)
        paths.append(path)
    return paths


def _distance(point, start, end):
    (px, py), (sx, sy), (ex, ey) = point, start, end
    dx, dy = ex - sx, ey - sy
    if dx == 0 and dy == 0:
        return ((px - sx) ** 2 + (py - sy) ** 2) ** 0.5
    return abs(dy * (px - sx) - dx * (py - sy)) / (dx * dx + dy * dy) ** 0.5


def simplify(points, tolerance):
    """Douglas-Peucker without recursion; the first and last points always stay."""
    keep = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        first, last = stack.pop()
        far, far_at = 0.0, None
        for i in range(first + 1, last):
            d = _distance(points[i], points[first], points[last])
            if d > far:
                far, far_at = d, i
        if far_at is not None and far > tolerance:
            keep.add(far_at)
            stack += [(first, far_at), (far_at, last)]
    return [points[i] for i in sorted(keep)]


def length(points):
    return sum(((points[i + 1][0] - points[i][0]) ** 2 + (points[i + 1][1] - points[i][1]) ** 2) ** 0.5 for i in range(len(points) - 1))


def chunks(points):
    """Pieces of at most MAX_POINTS points that share their joint, so a long outline stays one connected drawing."""
    if len(points) <= MAX_POINTS:
        return [points]
    return [points[i : i + MAX_POINTS] for i in range(0, len(points) - 1, MAX_POINTS - 1) if len(points[i : i + MAX_POINTS]) > 1]


def trace(gray, max_strokes):
    """At most `max_strokes` polylines (lists of (x, y) pixels) outlining the picture, the longest kept first."""
    if not gray or not gray[0] or max_strokes < 1:
        return []
    smooth = blur(gray)
    outlines = [path for cut in thresholds(smooth) for path in contours(smooth, cut)]
    best = []
    for tolerance in SIMPLIFY_STEPS:
        pieces = [piece for path in outlines if length(path) >= MIN_LENGTH for piece in chunks(simplify(path, tolerance)) if len(piece) > 1]
        best = pieces
        if len(pieces) <= max_strokes:
            return pieces
    return sorted(best, key=length, reverse=True)[:max_strokes]
