"""The sqlite tables every polling bot here keeps: a cursor, and the dedupe keys already announced."""

from slimbots import KeyValueTable, LedgerTable

STATE = KeyValueTable("state")
ANNOUNCED = LedgerTable("announced")
CURSOR_KEY = "history_cursor"


def init_db(conn, extra_schema=""):
    STATE.create(conn)
    ANNOUNCED.create(conn)
    conn.executescript(extra_schema)
    conn.commit()


def get_cursor(conn):
    value = STATE.get(conn, CURSOR_KEY)
    return int(value) if value is not None else None


def advance_cursor(conn, record_id):
    """Only ever moves forward, so a retried batch cannot rewind it."""
    STATE.advance(conn, CURSOR_KEY, int(record_id))
    conn.commit()


def get_announced(conn, key):
    """`(detail, at)` for a dedupe key, or None."""
    return ANNOUNCED.get(conn, key)


def mark_announced(conn, marks):
    """Records `(key, detail)` pairs; a key seen again keeps its place with the newer detail and time."""
    ANNOUNCED.add_many(conn, marks, replace=True)
    conn.commit()
