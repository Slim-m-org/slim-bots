#!/usr/bin/env python3
"""The subject crop before tracing: box validation, the model call, the heuristic fallback and the log; run directly: python3 test_crop.py."""

import asyncio
import contextlib
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai  # noqa: E402
import crop  # noqa: E402
import test_bot as base  # noqa: E402
from test_ai import FREE  # noqa: E402
from test_bot import ANN, draw  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402


def picture(size=(300, 400)):
    from PIL import Image, ImageDraw

    image = Image.new("RGB", size, (255, 255, 255))
    ImageDraw.Draw(image).rectangle([size[0] * 0.3, size[1] * 0.1, size[0] * 0.7, size[1] * 0.4], outline=(0, 0, 0), width=5)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def test_a_good_box_is_padded_and_stays_inside_the_picture():
    x, y, w, h = crop.clamp_box({"x": 0.3, "y": 0.1, "w": 0.4, "h": 0.3})
    assert abs(x - (0.3 - 0.032)) < 1e-9 and abs(w - 0.4 * 1.16) < 1e-9 and y < 0.1 and h > 0.3
    left, top, w, h = crop.clamp_box({"x": 0.0, "y": 0.0, "w": 1.0, "h": 1.0})
    assert (left, top, w, h) == (0.0, 0.0, 1.0, 1.0)


def test_a_box_past_the_edge_is_clamped_and_a_tiny_one_is_widened():
    x, y, w, h = crop.clamp_box({"x": 0.8, "y": 0.9, "w": 0.9, "h": 0.9})
    assert x + w <= 1.0 + 1e-9 and y + h <= 1.0 + 1e-9 and w >= crop.MIN_SIDE and h >= crop.MIN_SIDE
    x, y, w, h = crop.clamp_box({"x": 0.5, "y": 0.5, "w": 0.01, "h": 0.01})
    assert w >= crop.MIN_SIDE and h >= crop.MIN_SIDE


def test_an_unusable_answer_is_not_a_box():
    for bad in ({}, {"x": "a", "y": 0, "w": 1, "h": 1}, {"x": 0, "y": 0, "w": 0, "h": 1}, {"x": float("nan"), "y": 0, "w": 1, "h": 1}, None, {"x": 0, "y": 0, "w": -1, "h": 1}):
        assert crop.clamp_box(bad) is None, bad


def test_the_heuristic_takes_the_centre_top_of_a_portrait_and_all_of_anything_else():
    assert crop.heuristic_box(600, 900) == (0.15, 0.0, 0.7, 0.7)
    assert crop.heuristic_box(900, 600) == (0.0, 0.0, 1.0, 1.0)
    assert crop.heuristic_box(500, 500) == (0.0, 0.0, 1.0, 1.0)


def test_cropping_cuts_the_picture_and_the_preview_is_small():
    from PIL import Image

    cropped = Image.open(io.BytesIO(crop.crop_bytes(picture((300, 400)), (0.15, 0.0, 0.7, 0.7))))
    assert cropped.size == (210, 280)
    data, media_type = crop.preview(picture((1600, 1200)))
    assert media_type == "image/jpeg" and max(Image.open(io.BytesIO(data)).size) <= crop.PREVIEW_SIDE


def fake_client(answer, calls):
    class Block:
        type, name = "tool_use", "crop"
        input = answer

    class Message:
        content = [Block()]

    async def create(**kwargs):
        calls.append(kwargs)
        return Message()

    return type("C", (), {"messages": type("M", (), {"create": staticmethod(create)})()})()


def test_locate_makes_one_forced_tool_call_with_the_image_and_a_short_budget():
    calls = []
    planner = ai.AnthropicPlanner("sk-secret", "claude-sonnet-4-6", 16000, client=fake_client({"x": 0.2, "y": 0.1, "w": 0.5, "h": 0.4}, calls))
    box = asyncio.run(planner.locate("barack obama", b"jpg", "image/jpeg"))
    assert box == {"x": 0.2, "y": 0.1, "w": 0.5, "h": 0.4} and len(calls) == 1
    call = calls[0]
    assert call["tool_choice"] == {"type": "tool", "name": "crop"} and call["max_tokens"] == ai.CROP_TOKENS and [t["name"] for t in call["tools"]] == ["crop"]
    assert "thinking" not in call and [b["type"] for b in call["messages"][0]["content"]] == ["image", "text"]
    assert "barack obama" in call["messages"][0]["content"][1]["text"] and "sk-secret" not in str(call)


def test_locate_returns_none_on_any_failure_or_no_tool_call():
    class Boom(Exception):
        pass

    async def create(**kwargs):
        raise Boom("401 sk-secret")

    failing = ai.AnthropicPlanner("k", "m", 16000, client=type("C", (), {"messages": type("M", (), {"create": staticmethod(create)})()})())
    assert asyncio.run(failing.locate("x", b"jpg")) is None
    empty = type("C", (), {"messages": type("M", (), {"create": staticmethod(lambda **k: _none())})()})()
    assert asyncio.run(ai.AnthropicPlanner("k", "m", 16000, client=empty).locate("x", b"jpg")) is None


async def _none():
    return type("Msg", (), {"content": []})()


class LocatingPlanner:
    def __init__(self, answer):
        self.answer, self.asked = answer, []

    async def locate(self, subject, picture, media_type):
        self.asked.append((subject, media_type))
        return self.answer


def rig_for_crop(planner):
    rig = base.Rig()
    draw.planner, draw.AI_DAILY_CAP = planner, 100
    rig.png = picture((300, 400))
    base.sources.fetch_picture = lambda get, url: rig.png
    seen = []
    original = draw.photo.trace_photo
    draw.photo.trace_photo = lambda data, free, cap: seen.append(crop.size_of(data)) or original(data, free, cap)
    rig.seen = seen
    return rig


def run_photo(rig, text="!draw photo a cat"):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rig.say(text)
    return [line for line in out.getvalue().splitlines() if line.startswith("draw ")][-1]


def test_with_a_key_one_model_call_picks_the_crop_that_is_traced_and_logged():
    planner = LocatingPlanner({"x": 0.3, "y": 0.1, "w": 0.4, "h": 0.3})
    rig = rig_for_crop(planner)
    line = run_photo(rig)
    assert len(planner.asked) == 1 and planner.asked[0][1] == "image/jpeg"
    assert "crop=\"model 0.27" in line or "crop=model" in line
    width, height = rig.seen[0]
    assert width < 300 * 0.6 and height < 400 * 0.5


def test_without_a_key_the_heuristic_crop_is_used_and_logged():
    rig = rig_for_crop(None)
    line = run_photo(rig)
    assert 'crop="heuristic 0.15,0.00,0.70,0.70"' in line
    assert rig.seen[0] == (210, 280)


def test_a_failed_or_unusable_model_answer_falls_back_to_the_heuristic():
    for answer in (None, {"x": "no"}, {}):
        planner = LocatingPlanner(answer)
        rig = rig_for_crop(planner)
        line = run_photo(rig)
        assert "heuristic" in line and len(planner.asked) == 1 and rig.seen[0] == (210, 280)


def test_a_spent_daily_cap_skips_the_model_call():
    planner = LocatingPlanner({"x": 0.3, "y": 0.1, "w": 0.4, "h": 0.3})
    rig = rig_for_crop(planner)
    draw.AI_DAILY_CAP = 0
    line = run_photo(rig)
    assert planner.asked == [] and "heuristic" in line


def test_dots_are_cropped_too():
    planner = LocatingPlanner({"x": 0.3, "y": 0.1, "w": 0.4, "h": 0.3})
    rig = rig_for_crop(planner)
    line = run_photo(rig, "!draw dots a cat")
    assert "crop=" in line and len(planner.asked) == 1


def test_the_ranking_leans_on_the_centre_less_than_before():
    assert draw.photo.CENTRE_WEIGHT <= 0.2


if __name__ == "__main__":
    run_module_tests(globals())
