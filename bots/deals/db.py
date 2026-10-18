"""The deals bot's sqlite tables: posted freebies, price watches and the channel config."""

import time

KEEP_SEEN = 2000
MISSING_AFTER = 3
WATCH_COLUMNS = "id, user_id, app_id, name, target_price_cents, currency, last_price_cents, notified_price_cents, last_notified_at, misses"


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS freebies_seen (
            store TEXT NOT NULL, offer_id TEXT NOT NULL, title TEXT NOT NULL, at INTEGER NOT NULL, PRIMARY KEY (store, offer_id)
        );
        CREATE TABLE IF NOT EXISTS watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, app_id INTEGER NOT NULL, name TEXT NOT NULL,
            target_price_cents INTEGER NOT NULL, currency TEXT NOT NULL, last_price_cents INTEGER,
            notified_price_cents INTEGER, last_notified_at INTEGER, misses INTEGER NOT NULL DEFAULT 0,
            created_at INTEGER NOT NULL, UNIQUE (user_id, app_id)
        );
        CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        """
    )
    conn.commit()


def get_config(conn, key, default=None):
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_config(conn, key, value):
    conn.execute("INSERT INTO config (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, str(value)))
    conn.commit()


def is_seen(conn, store, offer_id):
    return conn.execute("SELECT 1 FROM freebies_seen WHERE store = ? AND offer_id = ?", (store, offer_id)).fetchone() is not None


def mark_seen(conn, store, offer_id, title):
    conn.execute("INSERT OR IGNORE INTO freebies_seen (store, offer_id, title, at) VALUES (?, ?, ?, ?)", (store, offer_id, title, int(time.time())))
    conn.execute("DELETE FROM freebies_seen WHERE rowid NOT IN (SELECT rowid FROM freebies_seen ORDER BY at DESC, rowid DESC LIMIT ?)", (KEEP_SEEN,))
    conn.commit()


def _watch(row):
    return dict(zip(WATCH_COLUMNS.split(", "), row))


def add_watch(conn, user_id, app_id, name, target_cents, currency, price_cents):
    """Returns (id, created); watching the same app again updates the target and re-arms the ping."""
    now = int(time.time())
    existing = conn.execute("SELECT id FROM watches WHERE user_id = ? AND app_id = ?", (user_id, app_id)).fetchone()
    if existing:
        conn.execute(
            "UPDATE watches SET name = ?, target_price_cents = ?, currency = ?, last_price_cents = ?, notified_price_cents = NULL, misses = 0 WHERE id = ?",
            (name, target_cents, currency, price_cents, existing[0]),
        )
        conn.commit()
        return existing[0], False
    cur = conn.execute(
        "INSERT INTO watches (user_id, app_id, name, target_price_cents, currency, last_price_cents, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (user_id, app_id, name, target_cents, currency, price_cents, now),
    )
    conn.commit()
    return cur.lastrowid, True


def list_watches(conn, user_id=None):
    where, args = ("WHERE user_id = ?", (user_id,)) if user_id else ("", ())
    return [_watch(r) for r in conn.execute(f"SELECT {WATCH_COLUMNS} FROM watches {where} ORDER BY id", args)]


def count_watches(conn, user_id):
    return conn.execute("SELECT COUNT(*) FROM watches WHERE user_id = ?", (user_id,)).fetchone()[0]


def remove_watch(conn, watch_id, user_id):
    removed = conn.execute("DELETE FROM watches WHERE id = ? AND user_id = ?", (watch_id, user_id)).rowcount
    conn.commit()
    return bool(removed)


def record_price(conn, watch_id, price_cents, name, notified_cents, notified_now):
    """Stores the latest price; a price back above target clears the notified mark so the next drop pings again."""
    conn.execute(
        "UPDATE watches SET last_price_cents = ?, name = ?, misses = 0, notified_price_cents = ?,"
        " last_notified_at = CASE WHEN ? THEN ? ELSE last_notified_at END WHERE id = ?",
        (price_cents, name, notified_cents, notified_now, int(time.time()), watch_id),
    )
    conn.commit()


def record_miss(conn, watch_id):
    """Counts a day the app was not available; returns the new streak."""
    conn.execute("UPDATE watches SET misses = misses + 1 WHERE id = ?", (watch_id,))
    conn.commit()
    return conn.execute("SELECT misses FROM watches WHERE id = ?", (watch_id,)).fetchone()[0]
