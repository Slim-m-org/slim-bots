"""What makes a traced line worth drawing: length, wiggle, border and background rules, isolation and rounding."""

import math

BORDER_BAND = 0.05  # of a side: a straight line this close to the edge is the crop's border, not the subject
BORDER_LENGTH = 0.2  # of that side
STRAIGHT = 3.0  # pixels of bend a line may have and still count as straight
ISOLATED_LENGTH = 40  # pixels: a fragment shorter than this with no neighbour is noise
NEIGHBOUR_REACH = 14  # pixels
MIN_FRACTION = 0.04  # of the image diagonal: shorter lines are noise
WIGGLE = 2.0  # path length over end-to-end distance beyond which a short open line is jagged
WIGGLE_FRACTION = 0.2  # of the diagonal: longer lines may wander
BACKGROUND_LENGTH = 0.12  # of the diagonal: a straight axis-aligned line this long outside the middle is a curtain, flag or frame
CENTRAL = (0.2, 0.8)  # the middle 60 percent of each side
AXIS_DEGREES = 15
BACKGROUND_BEND = 0.06  # of its length a curtain edge may bow and still be called straight
SUBJECT_REACH = 1.45  # lines whose middle lies this far out on the subject ellipse are background


def length(path):
    return sum(math.dist(a, b) for a, b in zip(path, path[1:]))


def distance(point, start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    if dx == dy == 0:
        return math.dist(point, start)
    return abs(dy * point[0] - dx * point[1] + end[0] * start[1] - end[1] * start[0]) / math.hypot(dx, dy)


def is_straight(line, tolerance=STRAIGHT):
    return max((distance(p, line[0], line[-1]) for p in line), default=0.0) <= tolerance


def wiggly(path, diagonal):
    """True for a short open line that doubles back on itself: a jagged fragment rather than a contour."""
    total = length(path)
    span = math.dist(path[0], path[-1])
    if total >= WIGGLE_FRACTION * diagonal or span < 0.2 * total:
        return False
    return total / max(span, 1.0) > WIGGLE


def hugs_border(line, width, height):
    """True for a long, nearly straight line that runs along one edge of the picture (a frame or curtain edge cut by the crop)."""
    if not is_straight(line):
        return False
    xs, ys = [p[0] for p in line], [p[1] for p in line]
    along_x = max(ys) - min(ys) < STRAIGHT and (max(ys) < height * BORDER_BAND or min(ys) > height * (1 - BORDER_BAND)) and max(xs) - min(xs) >= width * BORDER_LENGTH
    along_y = max(xs) - min(xs) < STRAIGHT and (max(xs) < width * BORDER_BAND or min(xs) > width * (1 - BORDER_BAND)) and max(ys) - min(ys) >= height * BORDER_LENGTH
    return along_x or along_y


def background_straight(line, width, height):
    """True for a long, nearly straight, nearly vertical or horizontal line whose middle is outside the central 60 percent (a curtain, flag or frame)."""
    diagonal = math.hypot(width, height)
    if length(line) < BACKGROUND_LENGTH * diagonal or not is_straight(line, max(STRAIGHT * 2, BACKGROUND_BEND * length(line))):
        return False
    dx, dy = line[-1][0] - line[0][0], line[-1][1] - line[0][1]
    angle = math.degrees(math.atan2(abs(dy), abs(dx)))
    if min(angle, 90 - angle) > AXIS_DEGREES:
        return False
    mx, my = sum(p[0] for p in line) / len(line), sum(p[1] for p in line) / len(line)
    return not (CENTRAL[0] * width <= mx <= CENTRAL[1] * width and CENTRAL[0] * height <= my <= CENTRAL[1] * height)


def on_face(point, width, height):
    """True inside the ellipse where a portrait's face usually sits: the middle of the picture, a little above centre."""
    return ((point[0] - width / 2) / (0.3 * width)) ** 2 + ((point[1] - 0.42 * height) / (0.38 * height)) ** 2 <= 1.0


def subject_distance(line, width, height):
    """How far the line's middle is from the middle of the picture on an ellipse covering the subject: 1.0 is its edge."""
    mx, my = sum(p[0] for p in line) / len(line), sum(p[1] for p in line) / len(line)
    return math.hypot((mx - width / 2) / (0.42 * width), (my - 0.46 * height) / (0.56 * height))


def subject_weight(line, width, height):
    """1.0 on the subject, fading to 0.25 well outside it."""
    outside = subject_distance(line, width, height) - 1.0
    return 1.0 if outside <= 0 else max(0.25, 1.0 - 1.5 * outside)


def _along(line):
    """Points every few pixels along a polyline, so a fragment beside a long straight segment still finds it."""
    step = NEIGHBOUR_REACH / 2
    for a, b in zip(line, line[1:]):
        span = max(math.dist(a, b), 1e-9)
        for i in range(max(int(span // step), 1)):
            t = i * step / span
            yield a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
    yield line[-1]


def drop_isolated(lines):
    """The lines without short fragments that have no other line within NEIGHBOUR_REACH pixels."""
    cell = NEIGHBOUR_REACH
    owners = {}
    for index, line in enumerate(lines):
        for x, y in _along(line):
            owners.setdefault((int(x // cell), int(y // cell)), set()).add(index)
    kept = []
    for index, line in enumerate(lines):
        if length(line) >= ISOLATED_LENGTH:
            kept.append(line)
            continue
        near = set()
        for x, y in _along(line):
            cx, cy = int(x // cell), int(y // cell)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    near |= owners.get((cx + dx, cy + dy), set())
        if near - {index}:
            kept.append(line)
    return kept


def chaikin(line, rounds=1):
    """Corner cutting: each round replaces every corner with two points a quarter of the way along its sides, keeping the end points."""
    for _ in range(rounds):
        if len(line) < 3:
            return line
        cut = [line[0]]
        for a, b in zip(line, line[1:]):
            cut.append((0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]))
            cut.append((0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1]))
        cut.append(line[-1])
        line = cut
    return line
