"""A picture of a canvas region for the model, and the largest free rectangle in it; Pillow only."""

import io

from plan import Region

UNITS_PER_PIXEL = 4
CELL = 50  # canvas units per occupancy cell
MIN_FREE = (400, 300)
MAX_FREE = (1000, 800)
PAGE = 500
PAGES = 2
MAX_TILE_FETCHES = 12
PEN = {"annotation": (214, 94, 94), "amber": (214, 160, 40), "green": (70, 160, 90), "blue": (70, 120, 210), "purple": (140, 100, 200), "pink": (210, 100, 160)}
PAPER = (250, 250, 247)


class ScreenshotError(RuntimeError):
    pass


def _pillow():
    try:
        from PIL import Image, ImageDraw
    except ImportError as err:
        raise ScreenshotError("Pillow is not installed, so I cannot look at the canvas") from err
    return Image, ImageDraw


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


def largest_free(objects, window):
    """The biggest empty rectangle (capped at MAX_FREE, centred), or None when nothing reaches MIN_FREE."""
    grid = occupancy(objects, window)
    cols = len(grid[0]) if grid else 0
    heights, best = [0] * cols, (0, 0, 0, 0, 0)  # area, col, row, width, height in cells
    for row, cells in enumerate(grid):
        heights = [0 if cell else h + 1 for cell, h in zip(cells, heights)]
        stack = []
        for col in range(cols + 1):
            current = heights[col] if col < cols else 0
            start = col
            while stack and stack[-1][1] >= current:
                begin, height = stack.pop()
                if height * (col - begin) > best[0]:
                    best = (height * (col - begin), begin, row - height + 1, col - begin, height)
                start = begin
            stack.append((start, current))
    _, col, row, width, height = best
    free_w, free_h = width * CELL, height * CELL
    if free_w < MIN_FREE[0] or free_h < MIN_FREE[1]:
        return None
    use_w, use_h = min(free_w, MAX_FREE[0]), min(free_h, MAX_FREE[1])
    return Region(window.x + col * CELL + (free_w - use_w) / 2, window.y + row * CELL + (free_h - use_h) / 2, use_w, use_h)


def _px(window, x, y):
    return (x - window.x) / UNITS_PER_PIXEL, (y - window.y) / UNITS_PER_PIXEL


def _draw_stroke(draw, window, obj):
    points = obj["props"].get("points") or []
    pairs = [_px(window, obj["x"] + points[i], obj["y"] + points[i + 1]) for i in range(0, len(points) - 1, 2)]
    color = PEN.get(obj["props"].get("color"), PEN["annotation"])
    if len(pairs) >= 2:
        draw.line(pairs, fill=color, width=max(1, round(float(obj["props"].get("width") or 3) / UNITS_PER_PIXEL)))


def _draw_box(draw, window, obj, outline, fill=None):
    left, top = _px(window, obj["x"], obj["y"])
    right, bottom = _px(window, obj["x"] + obj["w"], obj["y"] + obj["h"])
    draw.rectangle([left, top, max(right, left + 1), max(bottom, top + 1)], outline=outline, fill=fill)
    return left, top


def _paste_tile(image, window, obj, tile_bytes):
    from PIL import Image

    try:
        tile = Image.open(io.BytesIO(tile_bytes)).convert("RGB")
    except Exception:
        return False
    left, top = _px(window, obj["x"], obj["y"])
    size = (max(int(obj["w"] / UNITS_PER_PIXEL), 1), max(int(obj["h"] / UNITS_PER_PIXEL), 1))
    image.paste(tile.resize(size), (int(left), int(top)))
    return True


async def render(objects, window, free, fetch_tile=None):
    """PNG bytes of `window`: strokes in pen colours, shapes as outlines, text and notes as labelled boxes, image tiles shrunk, `free` dashed in green."""
    Image, ImageDraw = _pillow()
    image = Image.new("RGB", (int(window.w / UNITS_PER_PIXEL), int(window.h / UNITS_PER_PIXEL)), PAPER)
    draw = ImageDraw.Draw(image)
    step = 200
    for gx in range(int(window.x // step * step), int(window.x + window.w) + 1, step):
        px, _ = _px(window, gx, window.y)
        draw.line([(px, 0), (px, 6)], fill=(170, 170, 170))
        draw.text((px + 2, 8), str(gx), fill=(130, 130, 130))
    for gy in range(int(window.y // step * step), int(window.y + window.h) + 1, step):
        _, py = _px(window, window.x, gy)
        draw.line([(0, py), (6, py)], fill=(170, 170, 170))
        draw.text((8, py + 2), str(gy), fill=(130, 130, 130))
    fetched = 0
    for obj in objects:
        kind = obj.get("kind")
        if kind == "stroke":
            _draw_stroke(draw, window, obj)
        elif kind == "shape":
            _draw_box(draw, window, obj, PEN["blue"])
        elif kind in ("text", "note"):
            left, top = _draw_box(draw, window, obj, (120, 120, 120), (255, 244, 190) if kind == "note" else (235, 235, 235))
            draw.text((left + 2, top + 2), str(obj["props"].get("text", ""))[:24], fill=(60, 60, 60))
        elif kind == "image":
            tile = None
            attachment = obj["props"].get("attachment")
            if fetch_tile is not None and attachment and fetched < MAX_TILE_FETCHES:
                fetched += 1
                tile = await fetch_tile(attachment)
            if not (tile and _paste_tile(image, window, obj, tile)):
                _draw_box(draw, window, obj, (90, 90, 90), (200, 200, 205))
    if free is not None:
        left, top = _px(window, free.x, free.y)
        right, bottom = _px(window, free.x + free.w, free.y + free.h)
        for edge in (((left, top), (right, top)), ((right, top), (right, bottom)), ((right, bottom), (left, bottom)), ((left, bottom), (left, top))):
            draw.line(edge, fill=(40, 160, 70), width=1)
        draw.text((left + 4, top + 4), "draw inside this green box", fill=(40, 160, 70))
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()
