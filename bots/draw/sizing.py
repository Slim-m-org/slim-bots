"""How big and how fine to draw a picture, from the picture itself: detailed pictures get more rows and more of the free region; Pillow only."""

import pictures
from sources import SourceError

MEASURE_SIDE = 256
EDGE_LEVEL = 28  # edge strength (0..255) over which a pixel counts as detail
DETAIL_AT = (0.02, 0.22)  # share of detail pixels that maps to detail 0 and detail 1
MIN_SHARE = 0.7  # of the largest fitting drawing used for a picture with no detail
MAX_WIDTH = 1600.0  # board units: the widest a drawing is made


def measure(data):
    """Detail of a picture from 0 (smooth, simple) to 1 (busy, fine): the share of pixels on a clear edge in a downscaled copy."""
    try:
        from PIL import ImageFilter
    except ImportError as err:
        raise SourceError("Pillow is not installed on this bot, so it cannot read pictures") from err
    try:
        with pictures.open_picture(data) as picture:
            picture.draft("L", (MEASURE_SIDE * 2, MEASURE_SIDE * 2))
            gray = picture.convert("L")
            gray.thumbnail((MEASURE_SIDE, MEASURE_SIDE))
    except Exception as err:
        raise SourceError("the picture could not be read") from err
    edges = gray.filter(ImageFilter.GaussianBlur(0.8)).filter(ImageFilter.FIND_EDGES).crop((2, 2, gray.width - 2, gray.height - 2))
    pixels = list(edges.getdata())
    if not pixels:
        return 0.0
    share = sum(1 for v in pixels if v > EDGE_LEVEL) / len(pixels)
    low, high = DETAIL_AT
    return min(max((share - low) / (high - low), 0.0), 1.0)


def rows_for(detail, fewest, most):
    """The row count to start from: `fewest` for a simple picture up to `most` for a detailed one."""
    return round(fewest + (most - fewest) * detail)


def drawing_box(region, aspect, detail):
    """(x, y, w, h) of the drawing inside `region`, aspect (width over height) kept and centred: as large as fits, up to MAX_WIDTH wide, less for a simple picture."""
    w = min(region.w, MAX_WIDTH, region.h * aspect)
    w *= MIN_SHARE + (1.0 - MIN_SHARE) * detail
    h = w / aspect
    return region.x + (region.w - w) / 2, region.y + (region.h - h) / 2, w, h
