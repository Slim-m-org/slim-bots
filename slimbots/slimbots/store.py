"""A thread-offloaded sqlite handle for `bot.data_path`; see docs/framework.md."""

from __future__ import annotations

import asyncio
import functools
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Literal

IsolationLevel = Literal["DEFERRED", "EXCLUSIVE", "IMMEDIATE"]


class Store:
    """Owns one sqlite connection, but every query runs in a worker thread, never on the event loop."""

    def __init__(
        self, path: str, *, migrate: Callable[[sqlite3.Connection], None] | None = None,
        timeout: float = 30, isolation_level: IsolationLevel | None = None,
    ) -> None:
        self.path = path
        self._migrate = migrate
        self._timeout = timeout
        self._isolation_level: IsolationLevel | None = isolation_level
        self._conn: sqlite3.Connection | None = None
        self._executor: ThreadPoolExecutor | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> Store:
        """Opens the connection and runs the migration hook, both off the event loop; safe to call more than once."""
        async with self._lock:
            if self._conn is None:
                executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="slimbots-store")
                try:
                    self._conn = await asyncio.get_running_loop().run_in_executor(executor, self._open_sync)
                except BaseException:
                    executor.shutdown(wait=False)
                    raise
                self._executor = executor
        return self

    def _open_sync(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            self.path, timeout=self._timeout, isolation_level=self._isolation_level, check_same_thread=False
        )
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            if self._migrate is not None:
                self._migrate(conn)
        except BaseException:
            conn.close()
            raise
        return conn

    async def run(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Runs `fn(connection, *args, **kwargs)` on the single worker thread; a cancelled caller's call still finishes
        before the next call or `close()` starts, so none overlap."""
        executor = self._executor
        assert executor is not None, "run() needs an open store"
        return await asyncio.get_running_loop().run_in_executor(
            executor, functools.partial(self._run_sync, fn, args, kwargs)
        )

    def _run_sync(self, fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        """A raise between BEGIN and COMMIT would leave the shared connection in a transaction for every later call."""
        assert self._conn is not None, "run() needs an open store"
        try:
            return fn(self._conn, *args, **kwargs)
        except BaseException:
            if self._conn.in_transaction:
                self._conn.rollback()
            raise

    async def close(self) -> None:
        async with self._lock:
            executor, conn = self._executor, self._conn
            if executor is not None and conn is not None:
                await asyncio.get_running_loop().run_in_executor(executor, conn.close)
            if executor is not None:
                executor.shutdown(wait=False)
            self._conn = None
            self._executor = None

    @property
    def connection(self) -> sqlite3.Connection | None:
        """The raw `sqlite3.Connection`, for a caller not yet moved onto `run()` (a background sweep, a test)."""
        return self._conn
