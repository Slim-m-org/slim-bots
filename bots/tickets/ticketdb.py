"""The tickets bot's sqlite side and the transcript text, kept apart from the Discord-style plumbing."""

import sqlite3
import time
from datetime import datetime, timezone


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS tickets ("
        "number INTEGER PRIMARY KEY AUTOINCREMENT, opener_id TEXT NOT NULL, opener_name TEXT NOT NULL, "
        "channel_id TEXT UNIQUE, status TEXT NOT NULL, claimed_by TEXT, claimed_name TEXT, reason TEXT, "
        "created_at INTEGER NOT NULL, closed_at INTEGER, closed_by TEXT)"
    )
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS one_live_ticket ON tickets (opener_id) WHERE status IN ('opening', 'open', 'closing')")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS transcripts ("
        "ticket_number INTEGER PRIMARY KEY REFERENCES tickets (number), body TEXT NOT NULL, truncated INTEGER NOT NULL, saved_at INTEGER NOT NULL)"
    )
    conn.execute("DELETE FROM tickets WHERE status = 'opening'")
    conn.execute("UPDATE tickets SET status = 'open' WHERE status = 'closing'")
    conn.commit()


def reserve(conn, opener_id, opener_name):
    """Claims the member's one ticket slot; the new ticket number, or None when they already hold one."""
    try:
        cur = conn.execute(
            "INSERT INTO tickets (opener_id, opener_name, status, created_at) VALUES (?, ?, 'opening', ?)",
            (opener_id, opener_name, int(time.time())),
        )
    except sqlite3.IntegrityError:
        conn.rollback()
        return None
    conn.commit()
    return cur.lastrowid


def live_ticket(conn, opener_id):
    return conn.execute(
        "SELECT number, channel_id FROM tickets WHERE opener_id = ? AND status IN ('opening', 'open', 'closing')", (opener_id,)
    ).fetchone()


def release(conn, number):
    """Drops a reservation whose channel never got made."""
    conn.execute("DELETE FROM tickets WHERE number = ? AND status = 'opening'", (number,))
    conn.commit()


def activate(conn, number, channel_id):
    conn.execute("UPDATE tickets SET status = 'open', channel_id = ? WHERE number = ?", (channel_id, number))
    conn.commit()


def by_channel(conn, channel_id):
    return conn.execute(
        "SELECT number, opener_id, opener_name, status, claimed_by, claimed_name FROM tickets WHERE channel_id = ? "
        "AND status IN ('open', 'closing')",
        (channel_id,),
    ).fetchone()


def claim(conn, number, staff_id, staff_name):
    """True when this call took the claim; False when someone already holds it."""
    cur = conn.execute(
        "UPDATE tickets SET claimed_by = ?, claimed_name = ? WHERE number = ? AND status = 'open' AND claimed_by IS NULL",
        (staff_id, staff_name, number),
    )
    conn.commit()
    return cur.rowcount == 1


def set_reason(conn, number, reason):
    conn.execute("UPDATE tickets SET reason = ? WHERE number = ? AND status = 'open'", (reason, number))
    conn.commit()


def begin_close(conn, number):
    """True for exactly one of several racing presses."""
    cur = conn.execute("UPDATE tickets SET status = 'closing' WHERE number = ? AND status = 'open'", (number,))
    conn.commit()
    return cur.rowcount == 1


def abort_close(conn, number):
    conn.execute("UPDATE tickets SET status = 'open' WHERE number = ? AND status = 'closing'", (number,))
    conn.commit()


def finish_close(conn, number, closer_id, body, truncated):
    now = int(time.time())
    conn.execute("UPDATE tickets SET status = 'closed', closed_at = ?, closed_by = ? WHERE number = ?", (now, closer_id, number))
    conn.execute(
        "INSERT OR REPLACE INTO transcripts (ticket_number, body, truncated, saved_at) VALUES (?, ?, ?, ?)",
        (number, body, int(truncated), now),
    )
    conn.commit()


def get_transcript(conn, number):
    return conn.execute(
        "SELECT t.opener_name, x.body, x.truncated FROM transcripts x JOIN tickets t ON t.number = x.ticket_number "
        "WHERE x.ticket_number = ?",
        (number,),
    ).fetchone()


def format_line(message):
    stamp = datetime.fromtimestamp((message.get("created_at") or 0) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    who = message.get("author_display_name") or "deleted user"
    content = (message.get("content") or "").replace("\r", "").replace("\n", "\n    ")
    return f"[{stamp} UTC] {who}: {content}"


def build_transcript(messages, max_chars):
    """Oldest first, plain text; the text and whether `max_chars` cut it short."""
    text = "\n".join(format_line(m) for m in sorted(messages, key=lambda m: m.get("seq") or 0))
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True
