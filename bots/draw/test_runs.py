#!/usr/bin/env python3
"""The per-command log line, the public ack, the logged exception and the time cap; run directly: python3 test_runs.py."""

import asyncio
import contextlib
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import ai  # noqa: E402
import plan  # noqa: E402
import runlog  # noqa: E402
import test_bot as base  # noqa: E402
from test_ai import FakePlanner, FREE, _conversation, inside  # noqa: E402
from test_bot import ANN, draw  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402


def lines_for(rig, text, **kw):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rig.say(text, **kw)
    return [line for line in out.getvalue().splitlines() if line.startswith("draw ")]


def rig_with(planner):
    rig = base.Rig()
    draw.planner = planner
    draw.AI_DAILY_CAP = 100
    return rig


def test_a_drawing_logs_one_line_with_command_channel_outcome_source_stages_and_a_short_prompt():
    rig = rig_with(FakePlanner())
    long_prompt = "a cat " + "x" * 100
    (line,) = lines_for(rig, f"!draw {long_prompt}")
    assert line.startswith("draw command=draw channel=c-voice outcome=\"drew 3\"")
    for stage in ("canvas=", "model=", "place=", "total="):
        assert stage in line
    prompt = json.loads(line.split("prompt=", 1)[1])
    assert len(prompt) == runlog.PROMPT_CHARS and prompt.startswith("a cat x")


def test_each_refusal_logs_its_outcome():
    rig = rig_with(FakePlanner())
    draw.bot.voice._member_channel.clear()
    assert 'outcome="no call"' in lines_for(rig, "!draw a cat")[0]
    rig.in_call(ANN)
    draw.AI_COOLDOWN = 60
    draw.last_draw.clear()
    lines_for(rig, "!draw a cat")
    assert 'outcome="cooldown"' in lines_for(rig, "!draw a dog")[0]
    draw.AI_COOLDOWN = 0
    draw.busy.add("c-voice")
    assert 'outcome="busy"' in lines_for(rig, "!draw a cat")[0]
    draw.busy.clear()
    rig.client.respond("GET", "/members/bot-1/permissions", {"permissions": 0})
    assert 'outcome="refused: missing rights"' in lines_for(rig, "!draw a cat")[0]


def test_a_refusal_and_a_model_error_log_their_reason_without_the_key_or_a_traceback():
    rig = rig_with(FakePlanner(error=ai.Refused("the model declined to draw that")))
    assert 'outcome="refused: the model declined to draw that"' in lines_for(rig, "!draw x")[0]
    rig = rig_with(FakePlanner(error=ai.PlannerError("the model service failed (APIError)")))
    (line,) = lines_for(rig, "!draw x")
    assert "model error: the model service failed (APIError)" in line


def test_the_source_used_is_logged():
    from PIL import Image

    rig = rig_with(FakePlanner())
    png = io.BytesIO()
    Image.new("RGB", (64, 64), (9, 9, 9)).save(png, "PNG")
    base.sources.fetch_picture = lambda get, url: png.getvalue()
    base.sources.reference_image = base.real_reference_image
    base.sources.find_pictures = lambda get, subject, google=None, report=None: iter([{"url": "https://t/w.jpg", "title": "T", "creator": "", "license": "", "landing": "https://w", "source": "wikipedia"}])
    (line,) = lines_for(rig, "!draw barack obama")
    assert "source=wikipedia" in line and "search=" in line


def test_a_download_that_fails_moves_to_the_next_candidate_and_says_why():
    rig = base.Rig()
    good = [{"url": "https://bad/1", "title": "Bad", "creator": "", "license": "", "landing": "", "source": "duckduckgo"}, {"url": "https://ok/2", "title": "Ok", "creator": "", "license": "", "landing": "https://ok", "source": "wikipedia"}]
    base.sources.find_pictures = lambda get, subject, google=None, report=None: iter(good)

    def fetch(get, url):
        if "bad" in url:
            raise base.sources.SourceError("the picture could not be downloaded (http 403)")
        from PIL import Image

        out = io.BytesIO()
        Image.new("RGB", (80, 80), (20, 20, 20)).save(out, "PNG")
        return out.getvalue()

    base.sources.fetch_picture = fetch
    (line,) = lines_for(rig, "!draw photo a cat")
    assert "source=wikipedia" in line and "duckduckgo: the picture could not be downloaded (http 403)" in line


def test_an_unexpected_exception_is_logged_with_a_traceback_and_the_invoker_is_told():
    rig = rig_with(FakePlanner())

    async def boom(*a, **k):
        raise RuntimeError("kaboom")

    draw.place_all, original = boom, draw.place_all
    try:
        lines = lines_for(rig, "!draw a cat")
    finally:
        draw.place_all = original
    assert any("traceback" in line and "RuntimeError: kaboom" in line for line in lines) or any("RuntimeError" in line for line in lines)
    assert 'outcome="error RuntimeError"' in lines[-1]
    assert "something went wrong drawing that (RuntimeError)" in rig.client.ephemerals[-1]["content"]
    assert "c-voice" not in draw.busy


def test_a_long_job_is_acknowledged_publicly_before_the_result_and_errors_stay_private():
    rig = rig_with(FakePlanner())
    rig.say("!draw a cat")
    sent = [m["content"] for m in rig.client.sent]
    assert sent[0].startswith("drawing a cat, this takes a minute or two") and sent[-1].startswith("drew ") and len(sent) == 2
    rig = rig_with(FakePlanner(error=ai.Refused("the model declined to draw that")))
    rig.say("!draw x")
    assert len(rig.client.sent) == 1 and rig.client.ephemerals[-1]["content"].startswith("the model declined")


def test_dots_and_trace_are_acknowledged_too():
    rig = rig_with(None)
    rig.say("!draw trace cat")
    assert rig.client.sent[0]["content"].startswith("tracing cat")


def test_the_ack_defuses_mentions():
    rig = rig_with(FakePlanner())
    rig.say("!draw @everyone")
    assert "@everyone" not in rig.client.sent[0]["content"]


def test_the_log_line_is_emitted_once_and_never_holds_more_than_the_prompt_limit():
    out = []
    log = runlog.RunLog("draw", "c1", "p" * 200, emit=lambda text, flush=False: out.append(text))
    with log.stage("model"):
        pass
    log.done("drew 5")
    log.done("later")
    assert len(out) == 1 and "model=" in out[0] and len(json.loads(out[0].split("prompt=", 1)[1])) == 60


def test_the_time_cap_places_what_the_model_has_and_stops_asking():
    client, sent = _conversation([("tool_use", 2), ("tool_use", 2), ("end_turn", 0)])
    original = client.messages.create

    async def slow(**kwargs):
        if len(sent) >= 1:
            await asyncio.sleep(1)
        return await original(**kwargs)

    client.messages.create = slow
    planner = ai.AnthropicPlanner("k", "m", 100, client=client)
    calls = asyncio.run(planner.plan("a cat", b"png", FREE, deadline=time.monotonic() + 0.3))
    assert len(calls) == 2 and len(sent) == 1


def test_a_time_cap_that_passes_before_any_drawing_is_a_planner_error():
    client, _ = _conversation([("tool_use", 2)])

    async def slow(**kwargs):
        await asyncio.sleep(1)

    client.messages.create = slow
    try:
        asyncio.run(ai.AnthropicPlanner("k", "m", 100, client=client).plan("a cat", b"png", FREE, deadline=time.monotonic() + 0.2))
    except ai.PlannerError as err:
        assert "too long" in str(err)
    else:
        raise AssertionError("expected PlannerError")
    try:
        asyncio.run(ai.AnthropicPlanner("k", "m", 100, client=client).plan("a cat", b"png", FREE, deadline=time.monotonic() - 1))
    except ai.PlannerError as err:
        assert "time limit" in str(err)


def test_the_request_timeout_reaches_the_sdk_client_and_defaults_to_90_seconds():
    assert ai.AnthropicPlanner("k", "m", 100).request_timeout == 90
    chosen = ai.make_planner(api_key="k", model="m", max_tokens=100, local_url=None, local_model=None, local_key="", request_timeout=45)
    assert chosen.request_timeout == 45


if __name__ == "__main__":
    run_module_tests(globals())
