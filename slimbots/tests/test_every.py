import asyncio

import pytest

from slimbots import ApiError, Bot
from slimbots.lifecycle import Periodic, run_periodic


class StopLoop(Exception):
    pass


@pytest.fixture
def sleeps(monkeypatch):
    """Records each requested sleep and returns at once."""
    recorded = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds):
        recorded.append(seconds)
        await real_sleep(0)

    monkeypatch.setattr("slimbots.lifecycle.asyncio.sleep", fake_sleep)
    return recorded


async def drive(func, seconds=5, **kwargs):
    """Runs the loop until `func` raises StopLoop, which the loop must let out as a fatal type."""
    kwargs.setdefault("fatal", (StopLoop,))
    with pytest.raises(StopLoop):
        await asyncio.wait_for(run_periodic("job", Periodic(func, seconds, **kwargs)), 2)


async def test_a_failing_cycle_is_logged_and_retried_including_an_os_error(sleeps, capsys):
    calls = []

    async def job():
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk gone")
        if len(calls) == 2:
            raise RuntimeError("boom")
        raise StopLoop

    await drive(job)
    assert len(calls) == 3 and sleeps == [5, 5]
    err = capsys.readouterr().err
    assert "job: OSError: disk gone, retrying next cycle" in err and "job: RuntimeError: boom" in err


async def test_a_revoked_token_ends_the_loop(sleeps):
    async def job():
        raise ApiError(401, {"error": "invalid token"})

    with pytest.raises(ApiError):
        await asyncio.wait_for(run_periodic("job", Periodic(job, 5)), 2)


async def test_a_non_revoking_api_error_is_retried(sleeps):
    calls = []

    async def job():
        calls.append(1)
        if len(calls) == 1:
            raise ApiError(500, {"error": "boom"})
        raise StopLoop

    await drive(job)
    assert len(calls) == 2


async def test_only_the_declared_fatal_types_stop_the_loop(sleeps):
    class Rejected(Exception):
        pass

    async def job():
        raise Rejected("no")

    with pytest.raises(Rejected):
        await asyncio.wait_for(run_periodic("job", Periodic(job, 5, fatal=(Rejected,))), 2)


async def test_a_returned_number_is_a_minimum_wait(sleeps):
    results = iter([120, 1, None, True])

    async def job():
        try:
            return next(results)
        except StopIteration:
            raise StopLoop

    await drive(job)
    assert sleeps == [120, 5, 5, 5]


async def test_delay_first_sleeps_before_the_first_run(sleeps):
    order = []

    async def job():
        order.append(list(sleeps))
        raise StopLoop

    await drive(job, delay_first=True)
    assert order == [[5]]


async def test_a_callable_interval_is_read_every_cycle(sleeps):
    values = iter([3, 7])
    calls = []

    async def job():
        calls.append(1)
        if len(calls) == 3:
            raise StopLoop

    await drive(job, seconds=lambda: next(values))
    assert sleeps == [3, 7]


async def test_every_starts_once_across_reconnects():
    bot = Bot()
    runs = []

    @bot.every(60, name="tick")
    async def tick():
        runs.append(1)

    await bot._dispatch_event("on_connect")
    await bot._dispatch_event("on_connect")
    assert len(bot._background_tasks) == 1
    await asyncio.sleep(0)
    assert runs == [1]
    for task in list(bot._background_tasks):
        task.cancel()


async def test_every_can_wait_for_ready_instead_of_connect():
    bot = Bot()

    @bot.every(60, name="tick", on="ready")
    async def tick():
        pass

    await bot._dispatch_event("on_connect")
    assert not bot._background_tasks
    await bot._dispatch_event("on_ready")
    assert len(bot._background_tasks) == 1
    for task in list(bot._background_tasks):
        task.cancel()


async def test_every_stays_off_while_enabled_is_false():
    bot = Bot()
    on = []

    @bot.every(60, name="tick", enabled=lambda: bool(on))
    async def tick():
        pass

    await bot._dispatch_event("on_connect")
    assert not bot._background_tasks
    on.append(1)
    await bot._dispatch_event("on_connect")
    assert len(bot._background_tasks) == 1
    for task in list(bot._background_tasks):
        task.cancel()


async def test_before_runs_first_and_a_failure_leaves_the_task_for_the_next_connect():
    bot = Bot()
    order = []
    fail = [True]

    async def setup():
        order.append("before")
        if fail:
            raise RuntimeError("not yet")

    @bot.every(60, name="tick", before=setup)
    async def tick():
        order.append("tick")

    await bot._dispatch_event("on_connect")
    assert not bot._background_tasks
    fail.clear()
    await bot._dispatch_event("on_connect")
    await asyncio.sleep(0)
    assert order == ["before", "before", "tick"]
    for task in list(bot._background_tasks):
        task.cancel()


def test_a_name_can_only_be_registered_once():
    bot = Bot()
    bot.every(1, name="tick")(lambda: None)
    with pytest.raises(ValueError):
        bot.every(1, name="tick")(lambda: None)


async def test_a_fatal_error_stops_the_bot_like_any_background_task(sleeps):
    bot = Bot()

    @bot.every(60, name="tick", fatal=(StopLoop,))
    async def tick():
        raise StopLoop

    await bot._dispatch_event("on_connect")
    await asyncio.gather(*bot._background_tasks, return_exceptions=True)
    assert isinstance(bot._fatal_error, StopLoop)
