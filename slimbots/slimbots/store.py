"""A thread-offloaded sqlite handle for `bot.data_path`; see docs/framework.md."""

from __future__ import annotations

import asyncio
import functools
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Iterable, Literal

from .tables import KeyValueTable, LedgerTable

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

    async def kv(self, table: str = "kv") -> KeyValue:
        """A key/value table on this store, created if missing; an existing `(key, value)` table keeps its rows."""
        handle = KeyValue(self, KeyValueTable(table))
        await self.run(_committed(handle.table.create))
        return handle

    async def ledger(self, table: str, *, key: str = "key", detail: str | None = "detail", at: str = "at") -> Ledger:
        """An "already handled" ledger on this store, created if missing; name the columns of an existing table to reuse it."""
        handle = Ledger(self, LedgerTable(table, key=key, detail=detail, at=at))
        await self.run(_committed(handle.table.create))
        return handle

    @property
    def connection(self) -> sqlite3.Connection | None:
        """The raw `sqlite3.Connection`, for a caller not yet moved onto `run()` (a background sweep, a test)."""
        return self._conn


def _committed(fn: Callable[..., Any]) -> Callable[..., Any]:
    def run(conn: sqlite3.Connection, *args: Any, **kwargs: Any) -> Any:
        result = fn(conn, *args, **kwargs)
        conn.commit()
        return result
    return run


class KeyValue:
    """The async face of a `KeyValueTable`: each call is its own committed transaction."""

    def __init__(self, store: Store, table: KeyValueTable) -> None:
        self.store = store
        self.table = table

    async def get(self, key: str, default: str | None = None) -> str | None:
        return await self.store.run(self.table.get, key, default)

    async def set(self, key: str, value: object) -> None:
        await self.store.run(_committed(self.table.set), key, value)

    async def advance(self, key: str, value: int | str) -> bool:
        """Stores `value` only if it is ahead of the current one; returns whether it moved."""
        return await self.store.run(_committed(self.table.advance), key, value)


class Ledger:
    """The async face of a `LedgerTable`: each call is its own committed transaction."""

    def __init__(self, store: Store, table: LedgerTable) -> None:
        self.store = store
        self.table = table

    async def has(self, key: str) -> bool:
        return await self.store.run(self.table.has, key)

    async def unseen(self, keys: Iterable[str]) -> list[str]:
        return await self.store.run(self.table.unseen, list(keys))

    async def add(self, key: str, detail: str = "", *, replace: bool = False) -> bool:
        """Records `key`; returns True when it was new."""
        return await self.store.run(_committed(self.table.add), key, detail, replace=replace)

    async def prune(self, older_than: int) -> int:
        return await self.store.run(_committed(self.table.prune), older_than)
