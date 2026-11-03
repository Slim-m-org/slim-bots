"""Pen shading inside a face: dark features (sockets, lips, nostrils, shadows) are filled with parallel hatching, joined into zigzags."""

import math

SPACING = 4.5  # pixels between hatch lines at the working size
LIGHT = 0.55  # darkness above which the first hatch direction draws
DARK = 0.80  # darkness above which the cross direction draws too
MIN_RUN = 5  # pixels: shorter hatch runs are specks
JOIN = 2.2  # runs whose ends are within this many spacings are joined into one zigzag


def _darkness(gray, face):
    """The picture's darkness relative to the face's own tones: 0 at the face's lightest, 1 at its darkest."""
    from PIL import ImageFilter

    blurred = gray.filter(ImageFilter.GaussianBlur(1.4))
    px = blurred.load()
    w, h = blurred.size
    samples = sorted(px[x, y] for y in range(int(max(face.cy - face.ry, 0)), int(min(face.cy + face.ry, h)), 2) for x in range(int(max(face.cx - face.rx, 0)), int(min(face.cx + face.rx, w)), 2) if face.contains((x, y)))
    if len(samples) < 50:
        return None
    lo, hi = samples[len(samples) // 20], samples[len(samples) * 19 // 20]
    return px, w, h, lo, max(hi - lo, 1)


def _runs(dark, face, threshold, direction):
    """Hatch runs where darkness exceeds `threshold`, along `direction` (1: down-right, -1: down-left), inside the face."""
    px, w, h, lo, span = dark
    runs = []
    step = SPACING * math.sqrt(2)
    c = -h * 2.0
    while c < w + h:
        run = []
        for t in range(0, h):
            x = c + t if direction == 1 else c + (h - t)
            xi = int(x)
            if 0 <= xi < w and face.contains((x, t)) and 1 - (px[xi, t] - lo) / span > threshold:
                run.append((x, float(t)))
            else:
                if len(run) >= MIN_RUN:
                    runs.append((run[0], run[-1]))
                run = []
        if len(run) >= MIN_RUN:
            runs.append((run[0], run[-1]))
        c += step
    return runs


def _zigzag(runs):
    """Runs (in scan order) joined end to start into longer polylines when neighbours are close."""
    lines, current = [], []
    reach = SPACING * JOIN
    flip = False
    for a, b in runs:
        start, end = (b, a) if flip else (a, b)
        if current and math.dist(current[-1], start) <= reach:
            current += [start, end]
            flip = not flip
        else:
            if len(current) >= 2:
                lines.append(current)
            current = [a, b]
            flip = True
    if len(current) >= 2:
        lines.append(current)
    return lines


def shade_lines(gray, face):
    """Hatching polylines (pixels) for the dark parts of the face: one direction for mid darks, a cross direction for the darkest."""
    dark = _darkness(gray, face)
    if dark is None:
        return []
    lines = _zigzag(_runs(dark, face, LIGHT, 1)) + _zigzag(_runs(dark, face, DARK, -1))
    return lines
