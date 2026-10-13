"""The feeds bot's sqlite tables: subscriptions, the entries already posted, and failure bookkeeping."""

import re
import time

KEEP_SEEN = 1000
FAILING_AFTER = 3
FEED_COLUMNS = "id, channel_id, url, name, keywords, interval, etag, last_modified, next_due, failures, last_error"


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS feeds (
            id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL, url TEXT NOT NULL, name TEXT NOT NULL,
            keywords TEXT NOT NULL DEFAULT '', interval INTEGER NOT NULL, etag TEXT, last_modified TEXT,
            next_due INTEGER NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0, last_error TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL, created_at INTEGER NOT NULL, UNIQUE (channel_id, url)
        );
        CREATE TABLE IF NOT EXISTS seen (feed_id INTEGER NOT NULL, entry_key TEXT NOT NULL, at INTEGER NOT NULL, PRIMARY KEY (feed_id, entry_key));
        """
    )
    conn.commit()


def _feed(row):
    return dict(zip(FEED_COLUMNS.split(", "), row))


def add_feed(conn, channel_id, url, name, keywords, interval, created_by):
    """Returns the new id, or None when this channel already follows the URL."""
    now = int(time.time())
    cur = conn.execute(
        "INSERT OR IGNORE INTO feeds (channel_id, url, name, keywords, interval, next_due, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (channel_id, url, name, keywords, interval, now + interval, created_by, now),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount else None


def get_feed(conn, feed_id):
    row = conn.execute(f"SELECT {FEED_COLUMNS} FROM feeds WHERE id = ?", (feed_id,)).fetchone()
    return _feed(row) if row else None


def list_feeds(conn):
    return [_feed(r) for r in conn.execute(f"SELECT {FEED_COLUMNS} FROM feeds ORDER BY id")]


def due_feeds(conn, now):
    return [_feed(r) for r in conn.execute(f"SELECT {FEED_COLUMNS} FROM feeds WHERE next_due <= ? ORDER BY next_due", (now,))]


def remove_feed(conn, feed_id):
    conn.execute("DELETE FROM seen WHERE feed_id = ?", (feed_id,))
    removed = conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,)).rowcount
    conn.commit()
    return bool(removed)


def set_field(conn, feed_id, column, value):
    assert column in ("keywords", "interval"), "only these two are member-editable"
    updated = conn.execute(f"UPDATE feeds SET {column} = ? WHERE id = ?", (value, feed_id)).rowcount
    conn.commit()
    return bool(updated)


def seen_keys(conn, feed_id):
    return {r[0] for r in conn.execute("SELECT entry_key FROM seen WHERE feed_id = ?", (feed_id,))}


def mark_seen(conn, feed_id, keys):
    now = int(time.time())
    conn.executemany("INSERT OR IGNORE INTO seen (feed_id, entry_key, at) VALUES (?, ?, ?)", [(feed_id, k, now) for k in keys])
    conn.execute(
        "DELETE FROM seen WHERE feed_id = ? AND rowid NOT IN (SELECT rowid FROM seen WHERE feed_id = ? ORDER BY at DESC, rowid DESC LIMIT ?)",
        (feed_id, feed_id, KEEP_SEEN),
    )
    conn.commit()


def record_success(conn, feed_id, etag, last_modified, interval):
    conn.execute(
        "UPDATE feeds SET etag = ?, last_modified = ?, failures = 0, last_error = '', next_due = ? WHERE id = ?",
        (etag, last_modified, int(time.time()) + interval, feed_id),
    )
    conn.commit()


def record_failure(conn, feed_id, detail, interval):
    """Backs off by the failure count (capped at 6 intervals) so a dead feed is not hammered."""
    row = conn.execute("SELECT failures FROM feeds WHERE id = ?", (feed_id,)).fetchone()
    if row is None:
        return
    count = row[0] + 1
    conn.execute(
        "UPDATE feeds SET failures = ?, last_error = ?, next_due = ? WHERE id = ?",
        (count, detail[:200], int(time.time()) + interval * min(count + 1, 6), feed_id),
    )
    conn.commit()


def keyword_match(keywords, title, summary):
    """Comma-separated terms, any-of include (empty means everything); a term starting with `-` excludes."""
    terms = [t.strip().lower() for t in re.split(r"[,\n]", keywords) if t.strip()]
    haystack = f"{title} {summary}".lower()
    if any(t[1:].strip() in haystack for t in terms if t.startswith("-") and t[1:].strip()):
        return False
    include = [t for t in terms if not t.startswith("-")]
    return not include or any(t in haystack for t in include)
