"""A frame with an unexpected shape is logged and skipped; it never tears the connection down."""

import asyncio
import logging

import pytest

from slimbots import Bot
from slimbots.authors import AuthorFilter
from slimbots.http import ApiError
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

MALFORMED = [
    {"type": "voice.participant_joined", "user_id": "u1"},
    {"type": "voice.participant_left", "channel_id": "v1"},
    {"type": "typing.started", "channel_id": "c1"},
    {"type": "presence.changed"},
    {"type": "message.created", "channel_id": "c1", "message": "not a dict"},
]


class FakeGateway:
    def __init__(self, frames):
        self._frames = frames

    async def frames(self):
        for frame in self._frames:
            yield frame


def a_bot():
    client = FakeAsyncClient(me_id="bot-1")
    bot = Bot(prefix="!", channels={"c1"})
    bot.client, bot.space, bot.me_id = client, Space(client), "bot-1"
    bot.authors = AuthorFilter(client, space=bot.space)
    return bot


@pytest.mark.parametrize("frame", MALFORMED, ids=[f["type"] for f in MALFORMED])
async def test_a_malformed_frame_is_logged_with_its_traceback_and_does_not_raise(caplog, frame):
    bot = a_bot()

    async def listener(*_args):
        return None

    bot.event(listener, name="on_typing_started")
    bot.event(listener, name="on_presence_changed")
    with caplog.at_level(logging.ERROR):
        await bot._handle_frame(frame)
        await asyncio.gather(*list(bot._background_tasks))
    assert any(record.exc_info for record in caplog.records)
    assert bot._fatal_error is None


async def test_the_pump_keeps_reading_past_a_malformed_frame_so_the_socket_stays_up():
    bot = a_bot()
    seen = []

    @bot.event
    async def on_typing_started(event):
        seen.append(event.user_id)

    @bot.event
    async def on_presence_changed(event):
        seen.append(event.user_id)

    good = {"type": "typing.started", "channel_id": "c1", "user_id": "u1"}
    bad = {"type": "presence.changed"}
    await bot._pump_frames(FakeGateway([{"type": "voice.participant_joined"}, bad, good]))
    await asyncio.gather(*list(bot._background_tasks))
    assert seen == ["u1"]


async def test_a_revoked_token_still_ends_the_pump(monkeypatch):
    bot = a_bot()

    async def revoked(frame):
        raise ApiError(401, {"error": "revoked"})

    monkeypatch.setattr(bot, "_route_frame", revoked)
    with pytest.raises(ApiError) as raised:
        await bot._pump_frames(FakeGateway([{"type": "anything"}]))
    assert raised.value.status == 401
