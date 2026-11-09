"""The reconnect pacing and the connection lifecycle around it: growth, jitter, the stability-gated reset, and teardown."""

import asyncio
import logging

import pytest

from slimbots import Bot
from slimbots.http import ApiError
from slimbots.reconnect import Backoff
from slimbots.registration import RegistrationRejected

NO_JITTER = lambda low, high: 1.0  # noqa: E731


def waits_of(backoff, ended):
    """The waits a run of `ended` consecutive connections that never became ready would sleep."""
    out = []
    for _ in range(ended):
        backoff.connecting()
        out.append(backoff.next_wait())
    return out


def test_the_wait_doubles_from_the_base_up_to_the_cap_and_stays_there():
    backoff = Backoff(1.0, 60.0, jitter=NO_JITTER)
    assert waits_of(backoff, 9) == [1, 2, 4, 8, 16, 32, 60, 60, 60]


def test_each_wait_is_jittered_within_half_to_one_and_a_half_times_the_delay():
    asked = []

    def jitter(low, high):
        asked.append((low, high))
        return 0.5

    backoff = Backoff(4.0, 60.0, jitter=jitter)
    assert backoff.next_wait() == 2.0
    assert asked == [(0.5, 1.5)]


def test_the_real_jitter_spreads_bots_that_dropped_together():
    waits = {Backoff(8.0, 60.0).next_wait() for _ in range(50)}
    assert len(waits) > 10 and all(4.0 <= wait <= 12.0 for wait in waits)


def test_a_connection_that_was_ready_but_dropped_quickly_does_not_reset_the_wait():
    now = [0.0]
    backoff = Backoff(1.0, 60.0, clock=lambda: now[0], jitter=NO_JITTER)
    waits = []
    for _ in range(4):
        backoff.connecting()
        backoff.ready()
        now[0] += 5
        waits.append(backoff.next_wait())
    assert waits == [1, 2, 4, 8]


def test_a_connection_that_stayed_up_long_enough_starts_the_wait_over():
    now = [0.0]
    backoff = Backoff(1.0, 60.0, clock=lambda: now[0], jitter=NO_JITTER)
    waits_of(backoff, 4)
    backoff.connecting()
    backoff.ready()
    now[0] += 31
    assert backoff.next_wait() == 1
    assert waits_of(backoff, 2) == [2, 4]


def test_a_connection_that_never_got_ready_never_counts_as_stable():
    now = [0.0]
    backoff = Backoff(1.0, 60.0, clock=lambda: now[0], jitter=NO_JITTER)
    backoff.connecting()
    backoff.ready()
    backoff.next_wait()
    backoff.connecting()
    now[0] += 3600
    assert backoff.next_wait() == 2


def a_bot():
    bot = Bot(base_delay=1.0, max_delay=8.0)
    bot._jitter = NO_JITTER
    return bot


async def slept(monkeypatch):
    waits = []

    async def sleep(seconds):
        waits.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    return waits


async def test_the_run_loop_sleeps_one_two_four_then_holds_at_the_cap(monkeypatch):
    bot, attempts = a_bot(), []
    waits = await slept(monkeypatch)

    async def connect_once(mark_ready):
        attempts.append(1)
        if len(attempts) > 6:
            raise asyncio.CancelledError
        raise ApiError(None, "connection refused")

    monkeypatch.setattr(bot, "_connect_once", connect_once)
    with pytest.raises(asyncio.CancelledError):
        await bot._run_forever()
    assert waits == [1, 2, 4, 8, 8, 8]


async def test_a_server_that_keeps_dropping_a_ready_bot_is_not_retried_every_second(monkeypatch):
    bot, attempts, now = a_bot(), [], [0.0]
    bot._clock = lambda: now[0]
    waits = await slept(monkeypatch)

    async def connect_once(mark_ready):
        attempts.append(1)
        if len(attempts) > 5:
            raise asyncio.CancelledError
        mark_ready()
        now[0] += 2
        raise ApiError(None, "connection reset")

    monkeypatch.setattr(bot, "_connect_once", connect_once)
    with pytest.raises(asyncio.CancelledError):
        await bot._run_forever()
    assert waits == [1, 2, 4, 8, 8]


async def test_a_connection_that_lived_a_minute_resets_the_wait_on_its_next_drop(monkeypatch):
    bot, attempts, now = a_bot(), [], [0.0]
    bot._clock = lambda: now[0]
    waits = await slept(monkeypatch)

    async def connect_once(mark_ready):
        attempts.append(1)
        if len(attempts) > 4:
            raise asyncio.CancelledError
        if len(attempts) == 3:
            mark_ready()
            now[0] += 60
        raise ApiError(None, "connection reset")

    monkeypatch.setattr(bot, "_connect_once", connect_once)
    with pytest.raises(asyncio.CancelledError):
        await bot._run_forever()
    assert waits == [1, 2, 1, 2]


async def test_a_clean_close_is_retried_after_the_wait_without_a_failure_log(monkeypatch, caplog):
    bot, attempts = a_bot(), []
    waits = await slept(monkeypatch)

    async def connect_once(mark_ready):
        attempts.append(1)
        if len(attempts) > 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(bot, "_connect_once", connect_once)
    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await bot._run_forever()
    assert waits == [1, 2] and caplog.records == []


async def test_a_revoked_token_ends_the_run_at_once_without_a_wait(monkeypatch):
    bot = a_bot()
    waits = await slept(monkeypatch)

    async def connect_once(mark_ready):
        raise ApiError(401, {"error": "revoked"})

    monkeypatch.setattr(bot, "_connect_once", connect_once)
    assert await bot._run_forever() == 1
    assert waits == []


async def test_a_rejected_registration_ends_the_run_at_once_without_a_wait(monkeypatch):
    bot = a_bot()
    waits = await slept(monkeypatch)

    async def connect_once(mark_ready):
        raise RegistrationRejected("/bots/commands", ApiError(400, {"error": "bad"}))

    monkeypatch.setattr(bot, "_connect_once", connect_once)
    assert await bot._run_forever() == 1
    assert waits == []


async def test_the_failure_log_names_the_wait_that_was_actually_slept(monkeypatch, caplog):
    bot, attempts = a_bot(), []
    bot._jitter = lambda low, high: 1.5
    waits = await slept(monkeypatch)

    async def connect_once(mark_ready):
        attempts.append(1)
        if len(attempts) > 1:
            raise asyncio.CancelledError
        raise ApiError(None, "connection refused")

    monkeypatch.setattr(bot, "_connect_once", connect_once)
    with caplog.at_level(logging.WARNING), pytest.raises(asyncio.CancelledError):
        await bot._run_forever()
    assert waits == [1.5]
    assert "retrying in 1.5s" in caplog.records[0].getMessage()


class FakeGateway:
    def __init__(self, frames=(), then=None):
        self._frames, self._then = list(frames), then

    async def frames(self):
        for frame in self._frames:
            yield frame
        if self._then is not None:
            await self._then()


def served_bot(monkeypatch, catch_up):
    bot = Bot(channels={"c1"})
    monkeypatch.setattr(bot, "_catch_up", catch_up)
    return bot


async def test_serve_returns_when_the_socket_closes_and_cancels_a_replay_still_running(monkeypatch):
    replay_cancelled = asyncio.Event()

    async def endless_catch_up():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            replay_cancelled.set()
            raise

    bot = served_bot(monkeypatch, endless_catch_up)
    await asyncio.wait_for(bot._serve(FakeGateway(), lambda: None), 1)
    assert replay_cancelled.is_set()


async def test_serve_raises_the_replay_failure_and_stops_reading_frames(monkeypatch):
    pump_cancelled = asyncio.Event()

    async def failing_catch_up():
        raise ApiError(503, {"error": "unavailable"})

    async def never_ends():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pump_cancelled.set()
            raise

    bot = served_bot(monkeypatch, failing_catch_up)
    with pytest.raises(ApiError):
        await asyncio.wait_for(bot._serve(FakeGateway(then=never_ends), lambda: None), 1)
    assert pump_cancelled.is_set()


async def test_serve_raises_a_pump_failure_and_cancels_the_replay(monkeypatch):
    replay_cancelled = asyncio.Event()

    async def endless_catch_up():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            replay_cancelled.set()
            raise

    async def broken_socket():
        raise ConnectionError("socket broke")

    bot = served_bot(monkeypatch, endless_catch_up)
    with pytest.raises(ConnectionError):
        await asyncio.wait_for(bot._serve(FakeGateway(then=broken_socket), lambda: None), 1)
    assert replay_cancelled.is_set()


async def test_serve_marks_ready_once_the_replay_finishes_and_a_bad_frame_does_not_stop_it(monkeypatch):
    async def quick_catch_up():
        return None

    ready, seen = asyncio.Event(), []
    bot = served_bot(monkeypatch, quick_catch_up)

    async def on_frame(frame):
        seen.append(frame["type"])

    bot.event(on_frame)
    frames = [{"type": "voice.participant_joined"}, {"type": "typing.started", "channel_id": "c1", "user_id": "u"}]
    await asyncio.wait_for(bot._serve(FakeGateway(frames, then=ready.wait), ready.set), 1)
    await asyncio.gather(*list(bot._background_tasks))
    assert ready.is_set() and seen == ["voice.participant_joined", "typing.started"]
