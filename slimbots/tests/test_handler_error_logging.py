"""A bug in a handler or in the connection loop is logged with its traceback, an outside failure with one line."""

import asyncio
import logging

import pytest

from slimbots import ApiError
from slimbots.bot import Bot
from slimbots.lifecycle import guard_dispatch
from slimbots.testing import FakeAsyncClient


def _only(caplog, text):
    return next(r for r in caplog.records if text in r.getMessage())


async def test_a_handler_bug_keeps_its_traceback_and_names_the_handler(caplog):
    async def on_member_join(member):
        return {}["missing"]

    assert await guard_dispatch(on_member_join, None) is None
    record = _only(caplog, "unhandled error in")
    assert "on_member_join" in record.getMessage()
    assert record.levelno == logging.ERROR and isinstance(record.exc_info[1], KeyError)


async def test_an_api_failure_in_a_handler_is_one_line_without_a_traceback(caplog):
    async def on_raw_message(message):
        raise ApiError(403, {"error": "insufficient permissions"})

    await guard_dispatch(on_raw_message, {})
    record = _only(caplog, "unhandled error in")
    assert record.levelno == logging.WARNING and record.exc_info is None
    assert "ApiError: HTTP 403" in record.getMessage()


async def test_a_revoked_token_still_stops_the_dispatch(caplog):
    async def handler():
        raise ApiError(401, {"error": "invalid credentials"})

    with pytest.raises(ApiError):
        await guard_dispatch(handler)
    assert caplog.records == []


async def test_a_button_handler_bug_keeps_its_traceback(caplog):
    bot = Bot(prefix="!")
    bot.client = FakeAsyncClient()

    @bot.button("hit")
    async def hit(interaction):
        raise RuntimeError("boom")

    frame = {
        "type": "interaction.created", "interaction_id": "i1", "channel_id": "c1", "message_id": "m1",
        "custom_id": "hit", "user_id": "u1", "user_display_name": "Alice", "created_at": 1,
    }
    await bot._handle_frame(frame)
    await asyncio.gather(*list(bot._background_tasks))
    record = _only(caplog, "unhandled error in button `hit`")
    assert record.exc_info and "boom" in str(record.exc_info[1])


async def test_a_connect_bug_is_logged_with_its_traceback_on_every_retry(monkeypatch, caplog):
    bot = Bot()
    attempts = []

    async def connect_once(reset_delay):
        attempts.append(1)
        if len(attempts) == 1:
            raise KeyError("me_id")
        if len(attempts) == 2:
            raise ApiError(None, "connection refused")
        raise asyncio.CancelledError

    async def no_wait(seconds):
        return None

    monkeypatch.setattr(bot, "_connect_once", connect_once)
    monkeypatch.setattr(asyncio, "sleep", no_wait)
    with pytest.raises(asyncio.CancelledError):
        await bot._run_forever()
    bug, outage = [r for r in caplog.records if "connection failed" in r.getMessage()]
    assert bug.levelno == logging.ERROR and isinstance(bug.exc_info[1], KeyError)
    assert outage.levelno == logging.WARNING and outage.exc_info is None
