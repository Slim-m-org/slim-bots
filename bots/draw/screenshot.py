"""A picture of a canvas region for the model, and the largest free rectangle in it; Pillow only."""

import io

from plan import Region

UNITS_PER_PIXEL = 2
CELL = 50  # canvas units per occupancy cell
MIN_FREE = (400, 300)
MAX_FREE = (1000, 800)
PAGE = 500
PAGES = 2
MAX_TILE_FETCHES = 12
PEN = {"annotation": (214, 94, 94), "amber": (214, 160, 40), "green": (70, 160, 90), "blue": (70, 120, 210), "purple": (140, 100, 200), "pink": (210, 100, 160)}
PAPER = (250, 250, 247)
GUIDE = (150, 175, 210)


try:
    from PIL import Image as pil_image
    from PIL import ImageDraw as pil_draw
except ImportError:  # the bot still starts; a drawing request says Pillow is missing
    pil_image = pil_draw = None


class ScreenshotError(RuntimeError):
    pass


def _require_pillow():
    if pil_image is None:
        raise ScreenshotError("Pillow is not installed, so I cannot look at the canvas")


async def read_objects(canvas, window):
    """The live objects intersecting `window`, newest pages first up to PAGES reads."""
    found = []
    for _ in range(PAGES):
        page = await canvas.viewport(min_x=window.x, min_y=window.y, max_x=window.x + window.w, max_y=window.y + window.h, limit=PAGE)
        found.extend(page.get("objects", []))
        if not page.get("has_more"):
            break
    return found


def occupancy(objects, window):
    """A grid of cells, True where an object's box touches the cell."""
    cols, rows = int(window.w // CELL), int(window.h // CELL)
    grid = [[False] * cols for _ in range(rows)]
    for obj in objects:
        left, top = int((obj["x"] - window.x) // CELL), int((obj["y"] - window.y) // CELL)
        right, bottom = int((obj["x"] + obj["w"] - window.x) // CELL), int((obj["y"] + obj["h"] - window.y) // CELL)
        for row in range(max(top, 0), min(bottom, rows - 1) + 1):
            for col in range(max(left, 0), min(right, cols - 1) + 1):
                grid[row][col] = True
    return grid


def _scan_row(heights, row, best):
    """The best rectangle found so far after folding in a row's bar heights (largest rectangle in a histogram)."""
    stack = []
    for col in range(len(heights) + 1):
        current = heights[col] if col < len(heights) else 0
        start = col
        while stack and stack[-1][1] >= current:
            begin, height = stack.pop()
            area = height * (col - begin)
            if area > best[0]:
                best = (area, begin, row - height + 1, col - begin, height)
            start = begin
        stack.append((start, current))
    return best


def _centred_cap(window, best):
    """`best` (cells) as a region capped at MAX_FREE and centred in the cells it came from, or None when under MIN_FREE."""
    _, col, row, width, height = best
    free_w, free_h = width * CELL, height * CELL
    if free_w < MIN_FREE[0] or free_h < MIN_FREE[1]:
        return None
    use_w, use_h = min(free_w, MAX_FREE[0]), min(free_h, MAX_FREE[1])
    return Region(window.x + col * CELL + (free_w - use_w) / 2, window.y + row * CELL + (free_h - use_h) / 2, use_w, use_h)


def largest_free(objects, window):
    """The biggest empty rectangle (capped at MAX_FREE, centred), or None when nothing reaches MIN_FREE."""
    grid = occupancy(objects, window)
    heights = [0] * (len(grid[0]) if grid else 0)
    best = (0, 0, 0, 0, 0)  # area, col, row, width, height in cells
    for row, cells in enumerate(grid):
        heights = [0 if cell else h + 1 for cell, h in zip(cells, heights)]
        best = _scan_row(heights, row, best)
    return _centred_cap(window, best)


class View:
    """A canvas window drawn at `scale` canvas units per pixel."""

    def __init__(self, window, scale=UNITS_PER_PIXEL):
        self.window, self.scale = window, scale
        self.size = (max(int(window.w / scale), 1), max(int(window.h / scale), 1))

    def px(self, x, y):
        return (x - self.window.x) / self.scale, (y - self.window.y) / self.scale

    def box(self, obj):
        left, top = self.px(obj["x"], obj["y"])
        right, bottom = self.px(obj["x"] + obj["w"], obj["y"] + obj["h"])
        return left, top, max(right, left + 1), max(bottom, top + 1)


def fit_view(region, longest=768):
    """A view of `region` at no more than `longest` pixels on its long side, never magnified past 1 unit a pixel."""
    return View(region, max(max(region.w, region.h) / longest, 1.0))


def _pen(key):
    """A pen colour by palette name, or a `#rrggbb` hex string as the client reads it."""
    if isinstance(key, str) and len(key) == 7 and key.startswith("#"):
        try:
            return tuple(int(key[i : i + 2], 16) for i in (1, 3, 5))
        except ValueError:
            pass
    return PEN.get(key, PEN["annotation"])


def _draw_stroke(draw, view, obj):
    points = obj["props"].get("points") or []
    pairs = [view.px(obj["x"] + points[i], obj["y"] + points[i + 1]) for i in range(0, len(points) - 1, 2)]
    guide = bool(obj.get("guide"))
    color = GUIDE if guide else _pen(obj["props"].get("color"))
    width = 1 if guide else max(1, round(float(obj["props"].get("width") or 3) / view.scale))
    if len(pairs) >= 2:
        draw.line(pairs, fill=color, width=width, joint="curve")


def _draw_box(draw, view, obj, outline, fill=None):
    left, top, right, bottom = view.box(obj)
    draw.rectangle([left, top, right, bottom], outline=outline, fill=fill)
    return left, top


def _draw_shape(draw, view, obj):
    left, top, right, bottom = view.box(obj)
    shape, props = obj["props"].get("shape"), obj["props"]
    blue = GUIDE if obj.get("guide") else PEN["blue"]
    if shape == "ellipse":
        draw.ellipse([left, top, right, bottom], outline=blue)
    elif shape in ("line", "arrow"):
        start, end = [left, top], [right, bottom]
        if props.get("flip_x"):
            start[0], end[0] = end[0], start[0]
        if props.get("flip_y"):
            start[1], end[1] = end[1], start[1]
        draw.line([tuple(start), tuple(end)], fill=blue, width=2)
    else:
        draw.rectangle([left, top, right, bottom], outline=blue)


def _paste_tile(image, view, obj, tile_bytes):
    try:
        tile = pil_image.open(io.BytesIO(tile_bytes)).convert("RGB")
    except Exception:
        return False
    left, top = view.px(obj["x"], obj["y"])
    size = (max(int(obj["w"] / view.scale), 1), max(int(obj["h"] / view.scale), 1))
    image.paste(tile.resize(size), (int(left), int(top)))
    return True


def _draw_ruler(draw, view, step):
    window = view.window
    for gx in range(int(window.x // step * step), int(window.x + window.w) + 1, step):
        px, _ = view.px(gx, window.y)
        draw.line([(px, 0), (px, view.size[1])], fill=(228, 228, 224))
        draw.text((px + 2, 2), str(gx), fill=(130, 130, 130))
    for gy in range(int(window.y // step * step), int(window.y + window.h) + 1, step):
        _, py = view.px(window.x, gy)
        draw.line([(0, py), (view.size[0], py)], fill=(228, 228, 224))
        draw.text((2, py + 2), str(gy), fill=(130, 130, 130))


def _draw_label(draw, view, obj):
    note = obj.get("kind") == "note"
    left, top = _draw_box(draw, view, obj, (120, 120, 120), (255, 244, 190) if note else (235, 235, 235))
    draw.text((left + 2, top + 2), str(obj["props"].get("text", ""))[:24], fill=(60, 60, 60))


async def _draw_image(image, draw, view, obj, fetch_tile, fetched):
    """Draws an image tile (or a grey box when it cannot be read); returns whether a fetch was spent."""
    attachment = obj["props"].get("attachment")
    spend = fetch_tile is not None and bool(attachment) and fetched < MAX_TILE_FETCHES
    tile = await fetch_tile(attachment) if spend else None
    if not (tile and _paste_tile(image, view, obj, tile)):
        _draw_box(draw, view, obj, (90, 90, 90), (200, 200, 205))
    return spend


def _draw_free(draw, view, free):
    left, top = view.px(free.x, free.y)
    right, bottom = view.px(free.x + free.w, free.y + free.h)
    for edge in (((left, top), (right, top)), ((right, top), (right, bottom)), ((right, bottom), (left, bottom)), ((left, bottom), (left, top))):
        draw.line(edge, fill=(40, 160, 70), width=1)
    draw.text((left + 4, top + 4), "draw inside this green box", fill=(40, 160, 70))


async def _draw_object(image, draw, view, obj, fetch_tile, fetched):
    """Draws one canvas object; returns whether it spent a tile fetch."""
    kind = obj.get("kind")
    if kind == "stroke":
        _draw_stroke(draw, view, obj)
    elif kind == "shape":
        _draw_shape(draw, view, obj)
    elif kind in ("text", "note"):
        _draw_label(draw, view, obj)
    elif kind == "image":
        return await _draw_image(image, draw, view, obj, fetch_tile, fetched)
    return False


async def render(objects, view, free=None, fetch_tile=None, ruler=200, paper=PAPER):
    """PNG bytes of `view`: strokes in pen colours, shapes as their outline, text and notes as labelled boxes, image tiles shrunk, `free` outlined in green."""
    _require_pillow()
    if isinstance(view, Region):
        view = View(view)
    image = pil_image.new("RGB", view.size, paper)
    draw = pil_draw.Draw(image)
    _draw_ruler(draw, view, ruler)
    fetched = 0
    for obj in objects:
        fetched += await _draw_object(image, draw, view, obj, fetch_tile, fetched)
    if free is not None:
        _draw_free(draw, view, free)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()
