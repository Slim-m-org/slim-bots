import asyncio

import pytest

from slimbots import Bot, Canvas
from slimbots.context import Context


class FakeGateway:
    def __init__(self):
        self.sent = []

    async def send(self, frame):
        self.sent.append(frame)


async def test_send_frame_goes_through_the_open_gateway():
    bot = Bot()
    bot._gateway = FakeGateway()
    await bot.send_frame({"type": "typing", "channel_id": "c1"})
    assert bot._gateway.sent == [{"type": "typing", "channel_id": "c1"}]


async def test_send_frame_without_a_gateway_raises_a_clear_error():
    bot = Bot()
    with pytest.raises(RuntimeError, match="not connected"):
        await bot.send_frame({"type": "typing", "channel_id": "c1"})


async def test_start_typing_sends_a_typing_frame():
    bot = Bot()
    bot._gateway = FakeGateway()
    await bot.start_typing("c1")
    assert bot._gateway.sent == [{"type": "typing", "channel_id": "c1"}]


async def test_ctx_typing_sends_on_enter_and_refreshes_while_the_block_runs(monkeypatch):
    import slimbots.context as context_module

    monkeypatch.setattr(context_module, "TYPING_REFRESH_SECONDS", 0.02)
    bot = Bot()
    bot._gateway = FakeGateway()
    ctx = Context(bot=bot, message={"id": "m1"}, author=None, channel_id="c1")

    async with ctx.typing():
        await asyncio.sleep(0.07)

    assert len(bot._gateway.sent) >= 2
    assert all(f == {"type": "typing", "channel_id": "c1"} for f in bot._gateway.sent)


async def test_ctx_typing_stops_refreshing_once_the_block_exits(monkeypatch):
    import slimbots.context as context_module

    monkeypatch.setattr(context_module, "TYPING_REFRESH_SECONDS", 0.02)
    bot = Bot()
    bot._gateway = FakeGateway()
    ctx = Context(bot=bot, message={"id": "m1"}, author=None, channel_id="c1")

    async with ctx.typing():
        pass
    count_at_exit = len(bot._gateway.sent)
    await asyncio.sleep(0.06)
    assert len(bot._gateway.sent) == count_at_exit


class DropsAfterTheFirstFrame(FakeGateway):
    async def send(self, frame):
        if self.sent:
            raise ConnectionError("the socket dropped")
        await super().send(frame)


def _typing_ctx(monkeypatch, gateway):
    import slimbots.context as context_module

    monkeypatch.setattr(context_module, "TYPING_REFRESH_SECONDS", 0.01)
    bot = Bot()
    bot._gateway = gateway
    return Context(bot=bot, message={"id": "m1"}, author=None, channel_id="c1")


async def test_ctx_typing_on_a_closed_gateway_still_runs_the_body(monkeypatch, caplog):
    ctx = _typing_ctx(monkeypatch, None)
    ran = []
    async with ctx.typing():
        await asyncio.sleep(0.03)
        ran.append(True)
    assert ran == [True]
    assert any("could not show typing in c1" in r.getMessage() for r in caplog.records)


async def test_ctx_typing_whose_refresh_dies_mid_body_does_not_fail_a_finished_command(monkeypatch):
    ctx = _typing_ctx(monkeypatch, DropsAfterTheFirstFrame())
    async with ctx.typing():
        await asyncio.sleep(0.05)


async def test_ctx_typing_whose_refresh_dies_does_not_mask_the_bodys_own_error(monkeypatch):
    ctx = _typing_ctx(monkeypatch, DropsAfterTheFirstFrame())
    with pytest.raises(ValueError, match="the real failure"):
        async with ctx.typing():
            await asyncio.sleep(0.05)
            raise ValueError("the real failure")


async def test_canvas_send_cursor_and_stroke_preview_go_through_the_bots_gateway():
    bot = Bot()
    bot._gateway = FakeGateway()
    canvas = Canvas(bot.client, "c1", bot=bot)
    await canvas.send_cursor(1.5, 2.5)
    await canvas.send_stroke_preview("o1", [0, 0, 1, 1], ended=True)
    assert bot._gateway.sent == [
        {"type": "canvas.cursor", "channel_id": "c1", "x": 1.5, "y": 2.5},
        {"type": "canvas.stroke_preview", "channel_id": "c1", "object_id": "o1", "points": [0, 0, 1, 1], "ended": True},
    ]


async def test_canvas_without_a_bot_reference_refuses_a_live_signal():
    canvas = Canvas(None, "c1")
    with pytest.raises(RuntimeError, match="bot="):
        await canvas.send_cursor(0, 0)
