"""sqlite helpers for role menus; every function takes the connection `Store.run` hands it."""

import sqlite3
import time


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS menus (message_id TEXT PRIMARY KEY, channel_id TEXT NOT NULL, title TEXT NOT NULL, "
        "pick_one INTEGER NOT NULL, created_at INTEGER NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS menu_roles (message_id TEXT NOT NULL REFERENCES menus(message_id) ON DELETE CASCADE, "
        "role_id TEXT NOT NULL, label TEXT NOT NULL, position INTEGER NOT NULL, PRIMARY KEY (message_id, role_id))"
    )
    conn.commit()


def create_menu(conn, message_id, channel_id, title, pick_one, entries):
    conn.execute(
        "INSERT INTO menus (message_id, channel_id, title, pick_one, created_at) VALUES (?, ?, ?, ?, ?)",
        (message_id, channel_id, title, int(pick_one), int(time.time())),
    )
    conn.executemany(
        "INSERT INTO menu_roles (message_id, role_id, label, position) VALUES (?, ?, ?, ?)",
        [(message_id, role_id, label, i) for i, (label, role_id) in enumerate(entries)],
    )
    conn.commit()


def get_menu(conn, message_id):
    """`{channel_id, title, pick_one, roles: [(label, role_id)]}` in button order, or None."""
    head = conn.execute("SELECT channel_id, title, pick_one FROM menus WHERE message_id = ?", (message_id,)).fetchone()
    if head is None:
        return None
    found = conn.execute("SELECT label, role_id FROM menu_roles WHERE message_id = ? ORDER BY position", (message_id,)).fetchall()
    return {"channel_id": head[0], "title": head[1], "pick_one": bool(head[2]), "roles": [tuple(r) for r in found]}


def add_role(conn, message_id, label, role_id):
    position = conn.execute("SELECT COALESCE(MAX(position) + 1, 0) FROM menu_roles WHERE message_id = ?", (message_id,)).fetchone()[0]
    conn.execute("INSERT INTO menu_roles (message_id, role_id, label, position) VALUES (?, ?, ?, ?)", (message_id, role_id, label, position))
    conn.commit()


def remove_role(conn, message_id, role_id):
    conn.execute("DELETE FROM menu_roles WHERE message_id = ? AND role_id = ?", (message_id, role_id))
    conn.commit()


def delete_menu(conn, message_id):
    conn.execute("DELETE FROM menu_roles WHERE message_id = ?", (message_id,))
    conn.execute("DELETE FROM menus WHERE message_id = ?", (message_id,))
    conn.commit()


def list_menus(conn, channel_id):
    found = conn.execute("SELECT message_id, title, pick_one FROM menus WHERE channel_id = ? ORDER BY created_at", (channel_id,)).fetchall()
    return [(m, t, bool(p)) for m, t, p in found]
