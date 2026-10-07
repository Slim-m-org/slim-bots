import asyncio
import sqlite3
import threading
import time

import pytest

from slimbots import Store


async def start_then_cancel(store, fn, after=0.1):
    task = asyncio.create_task(store.run(fn))
    await asyncio.sleep(after)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_cancelled_run_does_not_let_the_next_run_overlap_it(tmp_path):
    active, most = 0, 0
    guard = threading.Lock()

    def work(seconds):
        def fn(_conn):
            nonlocal active, most
            with guard:
                active += 1
                most = max(most, active)
            time.sleep(seconds)
            with guard:
                active -= 1

        return fn

    store = await Store(str(tmp_path / "s.db")).open()
    await start_then_cancel(store, work(0.5))
    await store.run(work(0.05))
    await store.close()
    assert most == 1, f"{most} functions ran on the connection at once"


async def test_close_after_a_cancelled_run_waits_for_the_worker(tmp_path):
    outcome = []

    def slow(conn):
        time.sleep(0.4)
        try:
            conn.execute("SELECT 1")
            outcome.append("ok")
        except sqlite3.ProgrammingError as err:
            outcome.append(repr(err))

    store = await Store(str(tmp_path / "s.db")).open()
    await start_then_cancel(store, slow)
    await store.close()
    assert outcome == ["ok"]


async def test_a_store_can_be_closed_and_opened_again(tmp_path):
    store = await Store(str(tmp_path / "s.db")).open()
    await store.close()
    await store.open()
    assert await store.run(lambda conn: conn.execute("SELECT 1").fetchone()[0]) == 1
    await store.close()


async def test_the_timeout_argument_sets_the_busy_timeout(tmp_path):
    store = await Store(str(tmp_path / "s.db"), timeout=12).open()
    value = store.connection.execute("PRAGMA busy_timeout").fetchone()[0]
    await store.close()
    assert value == 12000


async def test_a_failing_migrate_closes_the_connection_it_opened(tmp_path, monkeypatch):
    opened = []
    real = sqlite3.connect

    class Spy:
        def __init__(self, conn):
            self.conn, self.closed = conn, False

        def __getattr__(self, name):
            return getattr(self.conn, name)

        def close(self):
            self.closed = True
            self.conn.close()

    def spy_connect(*args, **kwargs):
        spy = Spy(real(*args, **kwargs))
        opened.append(spy)
        return spy

    monkeypatch.setattr(sqlite3, "connect", spy_connect)

    def bad(_conn):
        raise RuntimeError("migration failed")

    store = Store(str(tmp_path / "s.db"), migrate=bad)
    with pytest.raises(RuntimeError):
        await store.open()
    assert opened and opened[0].closed
    assert store.connection is None
