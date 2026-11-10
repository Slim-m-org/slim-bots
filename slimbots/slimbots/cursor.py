"""A per-channel `seq` cursor in sqlite; see `catchup` for the async `/sync` calls built on it."""

from __future__ import annotations

import sqlite3


def init_table(conn: sqlite3.Connection, table: str = "cursors") -> None:
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {table} "
        "(channel_id TEXT PRIMARY KEY, after_seq INTEGER NOT NULL)"
    )
    conn.commit()


def get(conn: sqlite3.Connection, channel_id: str, table: str = "cursors") -> int | None:
    row = conn.execute(
        f"SELECT after_seq FROM {table} WHERE channel_id = ?", (channel_id,)
    ).fetchone()
    return row[0] if row else None


def _advance_sql(table: str) -> str:
    return (
        f"INSERT INTO {table} (channel_id, after_seq) VALUES (?, ?) "
        f"ON CONFLICT(channel_id) DO UPDATE SET after_seq = excluded.after_seq "
        f"WHERE excluded.after_seq > after_seq"
    )


def set(conn: sqlite3.Connection, channel_id: str, seq: int, table: str = "cursors") -> None:
    """Advances the stored cursor to `seq`, never backwards - a frame handled twice must not move it earlier."""
    conn.execute(_advance_sql(table), (channel_id, seq))
    conn.commit()


class Pending:
    """Seqs noted per message and written together, so a burst of messages costs one transaction."""

    def __init__(self, table: str = "cursors") -> None:
        self._table = table
        self._seqs: dict[str, int] = {}

    def note(self, channel_id: str, seq: int) -> None:
        if seq > self._seqs.get(channel_id, -1):
            self._seqs[channel_id] = seq

    def __len__(self) -> int:
        return len(self._seqs)

    def flush(self, conn: sqlite3.Connection) -> None:
        """Writes every noted seq in one transaction; they stay noted when the write fails."""
        if not self._seqs:
            return
        if not conn.in_transaction:
            conn.execute("BEGIN")
        try:
            conn.executemany(_advance_sql(self._table), list(self._seqs.items()))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        self._seqs.clear()
