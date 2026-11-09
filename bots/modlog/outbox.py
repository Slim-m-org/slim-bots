"""Moderation frames this bot has received but not yet posted, so a post that fails for good is retried, not lost."""

import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS unposted (
    seq INTEGER PRIMARY KEY,
    frame TEXT NOT NULL,
    tried_at INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);
"""
RETRY_BASE_SECONDS = 60
# Doubling stops here, so a refusal that lasts for days is still tried about once an hour.
MAX_DOUBLINGS = 6


def remember(conn, seq, frame, now):
    conn.execute("INSERT OR IGNORE INTO unposted (seq, frame, tried_at) VALUES (?, ?, ?)", (seq, json.dumps(frame), now))
    conn.commit()


def forget(conn, seq):
    conn.execute("DELETE FROM unposted WHERE seq = ?", (seq,))
    conn.commit()


def due(conn, now):
    """(seq, frame) for every frame whose next attempt is not in the future, oldest first."""
    rows = conn.execute(
        "SELECT seq, frame FROM unposted WHERE tried_at + ? * (1 << MIN(attempts, ?)) <= ? ORDER BY seq",
        (RETRY_BASE_SECONDS, MAX_DOUBLINGS, now),
    ).fetchall()
    return [(seq, json.loads(frame)) for seq, frame in rows]


def mark_tried(conn, seq, now):
    conn.execute("UPDATE unposted SET attempts = attempts + 1, tried_at = ? WHERE seq = ?", (now, seq))
    conn.commit()


def count(conn):
    return conn.execute("SELECT COUNT(*) FROM unposted").fetchone()[0]
