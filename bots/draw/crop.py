"""Choosing and applying a crop box (fractions of the picture) so a trace focuses on the subject: around a detected face, else on the busiest region."""

import io

import face
from sources import SourceError

BELOW = 2.0  # face heights kept below the face top: neck and shoulders
ABOVE = 0.55  # face heights kept above the face top: hair
WIDE = 2.3  # face widths across the crop
SALIENCY_GRID = 24
SALIENCY_LEVEL = 0.5  # of the busiest cell's weighted edge strength
CONCENTRATION = 0.6  # share of all edge strength the busiest region must hold, else the subject is not clearly localised
CENTRE_PRIOR = 0.5  # how much less a corner cell counts than the middle one
SALIENCY_PAD = 0.12
MIN_SIDE = 0.55  # a busiest-region crop is never narrower than this: a subject is rarely tiny, a texture patch is
WHOLE = (0.0, 0.0, 1.0, 1.0)


def _open(data):
    try:
        from PIL import Image
    except ImportError as err:
        raise SourceError("Pillow is not installed on this bot, so it cannot read pictures") from err
    try:
        return Image.open(io.BytesIO(data))
    except Exception as err:
        raise SourceError("the picture could not be read") from err


def size_of(data):
    with _open(data) as picture:
        return picture.size


def crop_bytes(data, box):
    """The picture cut to `box` (fractions), as PNG bytes; the whole picture when the box is all of it."""
    x, y, w, h = box
    with _open(data) as picture:
        rgb = picture.convert("RGB")
        width, height = rgb.size
        window = (round(x * width), round(y * height), round((x + w) * width), round((y + h) * height))
        out = io.BytesIO()
        rgb.crop(window).save(out, "PNG")
    return out.getvalue()


def describe(box):
    return ",".join(f"{v:.2f}" for v in box)


def box_around_face(box):
    """The head-and-shoulders crop (x, y, w, h fractions) for a face box, more below the face than above, kept inside the picture."""
    x, y, w, h = box
    cx = x + w / 2
    left, right = cx - WIDE * w / 2, cx + WIDE * w / 2
    top, bottom = y - ABOVE * h, y + BELOW * h
    left, top = max(left, 0.0), max(top, 0.0)
    right, bottom = min(right, 1.0), min(bottom, 1.0)
    return left, top, right - left, bottom - top


def face_in_crop(face_box, crop):
    """The face box (fractions of the picture) re-expressed as fractions of the crop, clamped inside it."""
    fx, fy, fw, fh = face_box
    cx, cy, cw, ch = crop
    x, y = (fx - cx) / cw, (fy - cy) / ch
    w, h = fw / cw, fh / ch
    left, top, right, bottom = max(x, 0.0), max(y, 0.0), min(x + w, 1.0), min(y + h, 1.0)
    return left, top, max(right - left, 0.01), max(bottom - top, 0.01)


def saliency_box(data):
    """The padded box (fractions) around the busiest region of the picture by edge strength, or None when that is nearly all of it."""
    from PIL import ImageFilter

    with _open(data) as picture:
        gray = picture.convert("L")
        gray.thumbnail((192, 192))
        edges = gray.filter(ImageFilter.GaussianBlur(1.0)).filter(ImageFilter.FIND_EDGES)
        edges = edges.crop((2, 2, edges.width - 2, edges.height - 2))  # the filter writes a bright frame at the border
    w, h = edges.size
    px = edges.load()
    n = SALIENCY_GRID
    cells = [[0.0] * n for _ in range(n)]
    for y in range(h):
        for x in range(w):
            cells[y * n // h][x * n // w] += px[x, y]
    cells = [
        [cells[r][c] * (1 - CENTRE_PRIOR * min(((r + 0.5) / n - 0.5) ** 2 + ((c + 0.5) / n - 0.5) ** 2, 0.5) * 2) for c in range(n)] for r in range(n)
    ]
    peak = max(max(row) for row in cells)
    if peak <= 0:
        return None
    start = max(((r, c) for r in range(n) for c in range(n)), key=lambda rc: cells[rc[0]][rc[1]])
    seen, stack = {start}, [start]
    while stack:
        r, c = stack.pop()
        for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            q = (r + dr, c + dc)
            if 0 <= q[0] < n and 0 <= q[1] < n and q not in seen and cells[q[0]][q[1]] >= SALIENCY_LEVEL * peak:
                seen.add(q)
                stack.append(q)
    total = sum(sum(row) for row in cells)
    if sum(cells[r][c] for r, c in seen) < CONCENTRATION * total:
        return None
    rows, cols = [r for r, _ in seen], [c for _, c in seen]
    x0, x1, y0, y1 = min(cols) / n, (max(cols) + 1) / n, min(rows) / n, (max(rows) + 1) / n
    padx, pady = (x1 - x0) * SALIENCY_PAD, (y1 - y0) * SALIENCY_PAD
    x0, x1, y0, y1 = max(x0 - padx, 0.0), min(x1 + padx, 1.0), max(y0 - pady, 0.0), min(y1 + pady, 1.0)
    x0, x1 = _at_least(x0, x1)
    y0, y1 = _at_least(y0, y1)
    box = (x0, y0, x1 - x0, y1 - y0)
    return None if box[2] * box[3] > 0.7 else box


def _at_least(low, high):
    """The interval widened about its middle to MIN_SIDE and kept inside 0..1."""
    if high - low >= MIN_SIDE:
        return low, high
    mid = (low + high) / 2
    low, high = mid - MIN_SIDE / 2, mid + MIN_SIDE / 2
    shift = max(0.0, -low) - max(0.0, high - 1.0)
    return low + shift, high + shift


def choose(data):
    """(crop box, face box in the crop or None, mode) for a picture: around its largest face, else its busiest region, else all of it."""
    found = face.largest_face(data)
    if found:
        box = box_around_face(found[0])
        return box, face_in_crop(found[0], box), "face"
    try:
        busy = saliency_box(data)
    except SourceError:
        busy = None
    return (busy, None, "saliency") if busy else (WHOLE, None, "whole")
