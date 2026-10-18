"""A key/value table and an "already handled" ledger; methods take the connection first and never commit."""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Iterable

_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(name: str) -> str:
    if not _IDENTIFIER.match(name):
        raise ValueError(f"`{name}` is not a plain table or column name")
    return name


class KeyValueTable:
    """`(key TEXT PRIMARY KEY, value TEXT NOT NULL)`; pass an existing table's name to keep its rows."""

    def __init__(self, table: str = "kv") -> None:
        self.table = _identifier(table)

    def create(self, conn: sqlite3.Connection) -> None:
        conn.execute(f"CREATE TABLE IF NOT EXISTS {self.table} (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def get(self, conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
        row = conn.execute(f"SELECT value FROM {self.table} WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, conn: sqlite3.Connection, key: str, value: object) -> None:
        conn.execute(
            f"INSERT INTO {self.table} (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )

    def advance(self, conn: sqlite3.Connection, key: str, value: int | str) -> bool:
        """Stores `value` only if it is ahead of the current one, so a retried batch cannot rewind a cursor; ints compare
        as numbers and strings as text. Returns whether it moved."""
        ahead = "CAST(excluded.value AS INTEGER) > CAST({0}.value AS INTEGER)" if isinstance(value, int) else "excluded.value > {0}.value"
        cur = conn.execute(
            f"INSERT INTO {self.table} (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value "
            f"WHERE {ahead.format(self.table)}",
            (key, str(value)),
        )
        return cur.rowcount == 1


class LedgerTable:
    """Keys already handled, each stamped with when; `detail=None` for a table without a detail column."""

    def __init__(self, table: str, *, key: str = "key", detail: str | None = "detail", at: str = "at") -> None:
        self.table, self.key, self.detail, self.at = _identifier(table), _identifier(key), detail and _identifier(detail), _identifier(at)

    def create(self, conn: sqlite3.Connection) -> None:
        detail = f", {self.detail} TEXT NOT NULL" if self.detail else ""
        conn.execute(f"CREATE TABLE IF NOT EXISTS {self.table} ({self.key} TEXT PRIMARY KEY{detail}, {self.at} INTEGER NOT NULL)")

    def has(self, conn: sqlite3.Connection, key: str) -> bool:
        return conn.execute(f"SELECT 1 FROM {self.table} WHERE {self.key} = ?", (key,)).fetchone() is not None

    def get(self, conn: sqlite3.Connection, key: str) -> tuple[str, int] | None:
        """`(detail, at)` for a key, or None; `detail` is "" in a table without that column."""
        detail = self.detail or "''"
        row = conn.execute(f"SELECT {detail}, {self.at} FROM {self.table} WHERE {self.key} = ?", (key,)).fetchone()
        return (row[0], row[1]) if row else None

    def keys(self, conn: sqlite3.Connection) -> set[str]:
        return {row[0] for row in conn.execute(f"SELECT {self.key} FROM {self.table}")}

    def unseen(self, conn: sqlite3.Connection, keys: Iterable[str]) -> list[str]:
        """The keys not in the ledger yet, in the order given."""
        return [key for key in keys if not self.has(conn, key)]

    def add(self, conn: sqlite3.Connection, key: str, detail: str = "", *, replace: bool = False, at: int | None = None) -> bool:
        """Records `key`; returns True when it was new. `replace` refreshes the detail and time of a key already there."""
        return self.add_many(conn, [(key, detail)], replace=replace, at=at) == 1

    def add_many(
        self, conn: sqlite3.Connection, entries: Iterable[str | tuple[str, str]], *, replace: bool = False, at: int | None = None,
    ) -> int:
        """Records each key, or `(key, detail)`; returns how many were new (or, with `replace`, written)."""
        stamp = int(time.time()) if at is None else at
        rows = [(e, "") if isinstance(e, str) else e for e in entries]
        columns, marks = [self.key], ["?"]
        if self.detail:
            columns.append(self.detail)
            marks.append("?")
        columns.append(self.at)
        marks.append("?")
        params = [(key, *((detail,) if self.detail else ()), stamp) for key, detail in rows]
        if replace:
            updates = ", ".join(f"{c} = excluded.{c}" for c in columns[1:])
            sql = f"INSERT INTO {self.table} ({', '.join(columns)}) VALUES ({', '.join(marks)}) ON CONFLICT({self.key}) DO UPDATE SET {updates}"
        else:
            sql = f"INSERT OR IGNORE INTO {self.table} ({', '.join(columns)}) VALUES ({', '.join(marks)})"
        before = conn.total_changes
        conn.executemany(sql, params)
        return conn.total_changes - before

    def prune(self, conn: sqlite3.Connection, older_than: int) -> int:
        """Forgets entries stamped before `older_than`; returns how many went."""
        return conn.execute(f"DELETE FROM {self.table} WHERE {self.at} < ?", (older_than,)).rowcount
