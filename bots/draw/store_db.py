"""The bot's own sqlite: drawings, the object ids kept for undo, and the day's model-call count."""

import time

SLOTS = 6


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS drawings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, query TEXT NOT NULL, slot INTEGER NOT NULL,
            created_at INTEGER NOT NULL, live INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS strokes (drawing_id INTEGER NOT NULL, object_id TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS strokes_by_drawing ON strokes (drawing_id);
        CREATE TABLE IF NOT EXISTS ai_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS serpapi_usage (month TEXT PRIMARY KEY, calls INTEGER NOT NULL);
        """
    )
    if "channel_id" not in {row[1] for row in conn.execute("PRAGMA table_info(drawings)")}:
        conn.execute("ALTER TABLE drawings ADD COLUMN channel_id TEXT NOT NULL DEFAULT ''")
    conn.commit()


def new_drawing(conn, user_id, query, channel_id, traced):
    """(drawing id, slot) or None when a traced drawing finds the channel's grid full; a model drawing takes no slot."""
    slot = -1
    if traced:
        taken = {r[0] for r in conn.execute("SELECT slot FROM drawings WHERE live = 1 AND channel_id = ?", (channel_id,))}
        slot = next((s for s in range(SLOTS) if s not in taken), None)
        if slot is None:
            return None
    cur = conn.execute(
        "INSERT INTO drawings (user_id, query, slot, created_at, channel_id) VALUES (?, ?, ?, ?, ?)", (user_id, query, slot, int(time.time()), channel_id)
    )
    conn.commit()
    return cur.lastrowid, slot


def add_stroke(conn, drawing_id, object_id):
    conn.execute("INSERT INTO strokes (drawing_id, object_id) VALUES (?, ?)", (drawing_id, object_id))
    conn.commit()


def add_strokes(conn, drawing_id, object_ids):
    conn.executemany("INSERT INTO strokes (drawing_id, object_id) VALUES (?, ?)", [(drawing_id, i) for i in object_ids])
    conn.commit()


def latest_live(conn, user_id, channel_id):
    return conn.execute(
        "SELECT id, query, channel_id FROM drawings WHERE live = 1 AND user_id = ? AND channel_id = ? ORDER BY id DESC LIMIT 1", (user_id, channel_id)
    ).fetchone()


def live_drawings(conn, channel_id):
    return [r[0] for r in conn.execute("SELECT id FROM drawings WHERE live = 1 AND channel_id = ? ORDER BY id", (channel_id,))]


def stroke_ids(conn, drawing_id):
    return [r[0] for r in conn.execute("SELECT object_id FROM strokes WHERE drawing_id = ? ORDER BY rowid", (drawing_id,))]


def retire(conn, drawing_id):
    conn.execute("UPDATE drawings SET live = 0 WHERE id = ?", (drawing_id,))
    conn.commit()


def take_ai_call(conn, day, cap):
    """Counts one model call against today's cap; False when the cap is spent."""
    row = conn.execute("SELECT calls FROM ai_usage WHERE day = ?", (day,)).fetchone()
    if (row[0] if row else 0) >= cap:
        return False
    conn.execute("INSERT INTO ai_usage (day, calls) VALUES (?, 1) ON CONFLICT (day) DO UPDATE SET calls = calls + 1", (day,))
    conn.commit()
    return True


def add_serpapi_calls(conn, month, count):
    """Adds `count` SerpAPI searches to `month`'s tally and returns the month's total, so the owner can watch the free quota."""
    conn.execute("INSERT INTO serpapi_usage (month, calls) VALUES (?, ?) ON CONFLICT (month) DO UPDATE SET calls = calls + ?", (month, count, count))
    conn.commit()
    return conn.execute("SELECT calls FROM serpapi_usage WHERE month = ?", (month,)).fetchone()[0]
