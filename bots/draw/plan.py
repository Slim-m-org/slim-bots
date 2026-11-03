"""The drawing tools a model may call, and the checks every call passes before anything reaches the canvas."""

import json
import math
from dataclasses import dataclass

PALETTE = ("annotation", "amber", "green", "blue", "purple", "pink")
SHAPES = ("rectangle", "ellipse", "line", "arrow")
TEXT_SIZES = ("small", "medium", "large")
MAX_POINTS = 400
CHUNK_POINTS = 150  # a longer stroke is split into pieces that share a joint, each far under the 4 KiB props limit
MAX_TEXT = 200
MAX_STROKE_WIDTH = 24.0
PROPS_LIMIT = 4096
TEXT_FONT = {"small": 14.0, "medium": 20.0, "large": 24.0}  # the client's world-unit font sizes
LINE_HEIGHT = 1.3
TEXT_PAD = 4.0
GLYPH_WIDTH = 0.62  # average glyph width in ems, a little wide on purpose so a box never clips
NOTE_FONT = 16.0
NOTE_WIDTH = 180.0
MAX_TEXT_WIDTH = 360.0


def text_box(text, font, max_width=MAX_TEXT_WIDTH):
    """(width, height) of a box that holds `text` at `font` units without the client clipping it."""
    one_line = len(text) * font * GLYPH_WIDTH + 2 * TEXT_PAD
    width = min(max(one_line, font + 2 * TEXT_PAD), max_width)
    per_line = max(int((width - 2 * TEXT_PAD) // (font * GLYPH_WIDTH)), 1)
    lines = max(-(-len(text) // per_line), 1) + text.count("\n")
    return width, lines * font * LINE_HEIGHT + 2 * TEXT_PAD


class PlanError(ValueError):
    """A tool call that cannot be placed, in words that go back to the model."""


@dataclass(frozen=True)
class Region:
    x: float
    y: float
    w: float
    h: float

    def contains(self, px, py):
        return self.x <= px <= self.x + self.w and self.y <= py <= self.y + self.h


@dataclass(frozen=True)
class Planned:
    """One canvas object, in the shape `Canvas.place` takes."""

    kind: str
    x: float
    y: float
    w: float
    h: float
    props: dict


def _tool(name, description, properties, required):
    return {"name": name, "description": description, "input_schema": {"type": "object", "properties": properties, "required": required}}


_COLOR = {"type": "string", "enum": list(PALETTE), "description": "pen colour name"}
_NUMBER = {"type": "number"}

TOOLS = [
    _tool(
        "draw_stroke",
        f"A freehand line through up to {MAX_POINTS} points in canvas units (use many points for smooth curves; longer lines are split for you); repeat the first point to close a contour. width 1 to {int(MAX_STROKE_WIDTH)}: thin for hatching and detail, thick for bold outlines.",
        {"points": {"type": "array", "items": {"type": "array", "items": _NUMBER, "minItems": 2, "maxItems": 2}, "minItems": 2, "maxItems": MAX_POINTS}, "color": _COLOR, "width": {"type": "number", "description": "line width, 1 to 24; default 3"}},
        ["points"],
    ),
    _tool(
        "draw_shape",
        "An outlined rectangle, ellipse, line or arrow filling the box x, y, w, h.",
        {"shape": {"type": "string", "enum": list(SHAPES)}, "x": _NUMBER, "y": _NUMBER, "w": _NUMBER, "h": _NUMBER, "flip_x": {"type": "boolean"}, "flip_y": {"type": "boolean"}},
        ["shape", "x", "y", "w", "h"],
    ),
    _tool(
        "add_text",
        "A text label whose top-left corner is x, y.",
        {"text": {"type": "string", "maxLength": MAX_TEXT}, "x": _NUMBER, "y": _NUMBER, "size": {"type": "string", "enum": list(TEXT_SIZES)}, "color": _COLOR},
        ["text", "x", "y"],
    ),
    _tool(
        "add_note",
        "A sticky note whose top-left corner is x, y.",
        {"text": {"type": "string", "maxLength": MAX_TEXT}, "x": _NUMBER, "y": _NUMBER},
        ["text", "x", "y"],
    ),
]
TOOL_NAMES = {tool["name"] for tool in TOOLS}


def _number(args, key):
    value = args.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PlanError(f"{key} must be a finite number")
    return float(value)


def _inside(region, x, y, w, h):
    if not (region.contains(x, y) and region.contains(x + w, y + h)):
        raise PlanError("it reaches outside the free region")


def _text(args):
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        raise PlanError("text must be a non-empty string")
    if len(text) > MAX_TEXT:
        raise PlanError(f"text is longer than {MAX_TEXT} characters")
    return text.replace("@", " at ")


def _color(args):
    color = args.get("color", PALETTE[0])
    if color not in PALETTE:
        raise PlanError(f"color must be one of {', '.join(PALETTE)}")
    return color


def _points(args):
    points = args.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise PlanError("a stroke needs at least 2 points")
    if len(points) > MAX_POINTS:
        raise PlanError(f"a stroke may have at most {MAX_POINTS} points; split it into several")
    pairs = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise PlanError("every point is a pair [x, y]")
        pairs.append((_number({"v": point[0]}, "v"), _number({"v": point[1]}, "v")))
    return pairs


def _width(args):
    width = args.get("width", 3.0)
    if isinstance(width, bool) or not isinstance(width, (int, float)) or not math.isfinite(width) or not 1 <= width <= MAX_STROKE_WIDTH:
        raise PlanError(f"width must be between 1 and {int(MAX_STROKE_WIDTH)}")
    return float(width)


def _one_stroke(pairs, width, color, region):
    low_x, low_y = min(p[0] for p in pairs), min(p[1] for p in pairs)
    high_x, high_y = max(p[0] for p in pairs), max(p[1] for p in pairs)
    _inside(region, low_x, low_y, high_x - low_x, high_y - low_y)
    flat = [round(v, 1) for p in pairs for v in (p[0] - low_x, p[1] - low_y)]
    return Planned("stroke", low_x, low_y, max(high_x - low_x, 0.5), max(high_y - low_y, 0.5), {"points": flat, "width": width, "color": color})


def _stroke(args, region):
    """One or more stroke objects: a long contour is cut into pieces that share an end point."""
    pairs, width, color = _points(args), _width(args), _color(args)
    pieces, start = [], 0
    while True:
        pieces.append(pairs[start : start + CHUNK_POINTS])
        start += CHUNK_POINTS - 1
        if start >= len(pairs) - 1:
            return [_one_stroke(piece, width, color, region) for piece in pieces]


def _shape(args, region):
    shape = args.get("shape")
    if shape not in SHAPES:
        raise PlanError(f"shape must be one of {', '.join(SHAPES)}")
    x, y, w, h = (_number(args, key) for key in ("x", "y", "w", "h"))
    if w < 1 or h < 1:
        raise PlanError("w and h must be at least 1")
    _inside(region, x, y, w, h)
    props = {"shape": shape}
    for flip in ("flip_x", "flip_y"):
        if args.get(flip) is True:
            props[flip] = True
    return [Planned("shape", x, y, w, h, props)]


def _label(args, region, kind):
    text = _text(args)
    x, y = _number(args, "x"), _number(args, "y")
    if kind == "note":
        w, h = text_box(text, NOTE_FONT, NOTE_WIDTH)
        props = {"text": text}
        h = max(h, 120.0)
    else:
        size = args.get("size", "medium")
        if size not in TEXT_SIZES:
            raise PlanError(f"size must be one of {', '.join(TEXT_SIZES)}")
        w, h = text_box(text, TEXT_FONT[size])
        props = {"text": text, "size": size, "color": _color(args)}
    _inside(region, x, y, w, h)
    return [Planned(kind, x, y, w, h, props)]


def validate_call(name, args, region):
    """The canvas objects for one tool call (a long stroke is several), or a `PlanError` saying what to fix."""
    if name not in TOOL_NAMES:
        raise PlanError(f"there is no tool called {name}")
    if not isinstance(args, dict):
        raise PlanError("arguments must be an object")
    if name == "draw_stroke":
        planned = _stroke(args, region)
    elif name == "draw_shape":
        planned = _shape(args, region)
    else:
        planned = _label(args, region, "note" if name == "add_note" else "text")
    if any(len(json.dumps(p.props)) >= PROPS_LIMIT for p in planned):
        raise PlanError("the object is larger than 4 KiB")
    return planned


def validate_plan(calls, region, cap):
    """(objects to place, problems) for a model's calls; a bad call is skipped, objects past `cap` dropped."""
    objects, problems = [], []
    for name, args in calls:
        try:
            made = validate_call(name, args, region)
        except PlanError as err:
            problems.append(f"{name}: {err}")
            continue
        if len(objects) + len(made) > cap:
            problems.append(f"more than {cap} objects; the rest were dropped")
            break
        objects.extend(made)
    return objects, problems
