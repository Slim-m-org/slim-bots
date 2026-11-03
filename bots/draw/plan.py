"""The drawing tools a model may call, and the checks every call passes before anything reaches the canvas."""

import json
import math
from dataclasses import dataclass

PALETTE = ("annotation", "amber", "green", "blue", "purple", "pink")
SHAPES = ("rectangle", "ellipse", "line", "arrow")
TEXT_SIZES = ("small", "medium", "large")
MAX_POINTS = 120
MAX_TEXT = 200
MAX_STROKE_WIDTH = 24.0
PROPS_LIMIT = 4096
NOTE_SIZE = (180.0, 120.0)


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
        f"A freehand line through at most {MAX_POINTS} points in canvas units; close the path by repeating the first point. Build pictures from many strokes.",
        {"points": {"type": "array", "items": {"type": "array", "items": _NUMBER, "minItems": 2, "maxItems": 2}, "minItems": 2, "maxItems": MAX_POINTS}, "color": _COLOR, "width": {"type": "number", "description": "line width, 1 to 24"}},
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


def _stroke(args, region):
    points = args.get("points")
    if not isinstance(points, list) or len(points) < 2:
        raise PlanError("a stroke needs at least 2 points")
    if len(points) > MAX_POINTS:
        raise PlanError(f"a stroke may have at most {MAX_POINTS} points")
    pairs = []
    for point in points:
        if not isinstance(point, (list, tuple)) or len(point) != 2:
            raise PlanError("every point is a pair [x, y]")
        pairs.append((_number({"v": point[0]}, "v"), _number({"v": point[1]}, "v")))
    width = args.get("width", 3.0)
    if isinstance(width, bool) or not isinstance(width, (int, float)) or not 1 <= width <= MAX_STROKE_WIDTH:
        raise PlanError(f"width must be between 1 and {int(MAX_STROKE_WIDTH)}")
    low_x, low_y = min(p[0] for p in pairs), min(p[1] for p in pairs)
    high_x, high_y = max(p[0] for p in pairs), max(p[1] for p in pairs)
    _inside(region, low_x, low_y, high_x - low_x, high_y - low_y)
    flat = [round(v, 1) for p in pairs for v in (p[0] - low_x, p[1] - low_y)]
    return Planned("stroke", low_x, low_y, max(high_x - low_x, 0.5), max(high_y - low_y, 0.5), {"points": flat, "width": float(width), "color": _color(args)})


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
    return Planned("shape", x, y, w, h, props)


def _label(args, region, kind):
    text = _text(args)
    x, y = _number(args, "x"), _number(args, "y")
    if kind == "note":
        w, h = NOTE_SIZE
        props = {"text": text}
    else:
        size = args.get("size", "medium")
        if size not in TEXT_SIZES:
            raise PlanError(f"size must be one of {', '.join(TEXT_SIZES)}")
        scale = {"small": 14, "medium": 20, "large": 28}[size]
        w, h = max(len(text), 1) * scale * 0.6, scale * 1.5
        props = {"text": text, "size": size, "color": _color(args)}
    _inside(region, x, y, w, h)
    return Planned(kind, x, y, w, h, props)


def validate_call(name, args, region):
    """The canvas object for one tool call, or a `PlanError` saying what to fix."""
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
    if len(json.dumps(planned.props)) >= PROPS_LIMIT:
        raise PlanError("the object is larger than 4 KiB")
    return planned


def validate_plan(calls, region, cap):
    """(objects to place, problems) for a model's calls; a bad call is skipped, calls past `cap` dropped."""
    objects, problems = [], []
    for name, args in calls:
        if len(objects) >= cap:
            problems.append(f"more than {cap} objects; the rest were dropped")
            break
        try:
            objects.append(validate_call(name, args, region))
        except PlanError as err:
            problems.append(f"{name}: {err}")
    return objects, problems
