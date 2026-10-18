"""bot-game-status's settings, stored servers and boards, and the status text and embed; see README.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" in docs/framework.md for why."""

import datetime
import re
import sqlite3

from slimbots import Embed

from queries import KINDS

MAX_SERVERS = 25
MAX_NAME = 40
HOST_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,253}$")
COLORS = {"all_up": 0x2ECC71, "mixed": 0xF1C40F, "all_down": 0xE74C3C}

GAMESTATUS_SERVERS = []
GAMESTATUS_CHANNELS = []
GAMESTATUS_INTERVAL = 60
GAMESTATUS_MIN_INTERVAL = 15
GAMESTATUS_TIMEOUT = 5.0
_config_errors = []


def configure(bot):
    global GAMESTATUS_SERVERS, GAMESTATUS_CHANNELS, GAMESTATUS_INTERVAL, GAMESTATUS_MIN_INTERVAL, GAMESTATUS_TIMEOUT
    GAMESTATUS_MIN_INTERVAL = max(5, bot.setting("GAMESTATUS_MIN_INTERVAL", 15, type=int))
    GAMESTATUS_INTERVAL = max(GAMESTATUS_MIN_INTERVAL, bot.setting("GAMESTATUS_INTERVAL", 60, type=int))
    GAMESTATUS_TIMEOUT = max(1.0, bot.setting("GAMESTATUS_TIMEOUT", 5.0, type=float))
    GAMESTATUS_CHANNELS = bot.setting("GAMESTATUS_CHANNELS", [], type=list)
    GAMESTATUS_SERVERS = []
    _config_errors.clear()
    for entry in bot.setting("GAMESTATUS_SERVERS", [], type=list):
        server, problem = parse_server_entry(entry)
        if server:
            GAMESTATUS_SERVERS.append(server)
        else:
            _config_errors.append(f"GAMESTATUS_SERVERS entry {entry!r}: {problem}")


def check_config():
    """A one-line reason the settings cannot run, or None."""
    return _config_errors[0] if _config_errors else None


def validate_server(name, kind, host, port):
    """Why these values cannot be a server, or None."""
    if not name or len(name) > MAX_NAME:
        return f"the name must be 1 to {MAX_NAME} characters."
    if kind not in KINDS:
        return f"the kind must be one of {', '.join(KINDS)}."
    if not HOST_PATTERN.match(host):
        return "the host must be a hostname or IP address."
    if not 1 <= port <= 65535:
        return "the port must be 1 to 65535."
    return None


def parse_server_entry(entry):
    """(server dict, None) for `name=kind:host:port`, else (None, why)."""
    name, _, rest = entry.partition("=")
    kind, _, address = rest.partition(":")
    host, _, port_text = address.rpartition(":")
    if not port_text.isdigit():
        return None, "expected name=kind:host:port"
    server = {"name": name.strip(), "kind": kind.strip().lower(), "host": host.strip(), "port": int(port_text)}
    problem = validate_server(server["name"], server["kind"], server["host"], server["port"])
    return (None, problem) if problem else (server, None)


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS servers (name TEXT PRIMARY KEY COLLATE NOCASE, kind TEXT NOT NULL, "
        "host TEXT NOT NULL, port INTEGER NOT NULL, position INTEGER NOT NULL)"
    )
    conn.execute("CREATE TABLE IF NOT EXISTS boards (channel_id TEXT PRIMARY KEY, message_id TEXT NOT NULL)")
    conn.commit()


def txn_upsert_server(conn, server):
    """Adds or replaces a server by name; False when a new one would pass MAX_SERVERS."""
    exists = conn.execute("SELECT 1 FROM servers WHERE name = ?", (server["name"],)).fetchone()
    count = conn.execute("SELECT COUNT(*) FROM servers").fetchone()[0]
    if not exists and count >= MAX_SERVERS:
        return False
    position = conn.execute("SELECT COALESCE(MAX(position), 0) + 1 FROM servers").fetchone()[0]
    conn.execute(
        "INSERT INTO servers (name, kind, host, port, position) VALUES (?, ?, ?, ?, ?) ON CONFLICT(name) "
        "DO UPDATE SET kind = excluded.kind, host = excluded.host, port = excluded.port",
        (server["name"], server["kind"], server["host"], server["port"], position),
    )
    conn.commit()
    return True


def txn_remove_server(conn, name):
    removed = conn.execute("DELETE FROM servers WHERE name = ?", (name,)).rowcount
    conn.commit()
    return removed > 0


def txn_list_servers(conn):
    rows = conn.execute("SELECT name, kind, host, port FROM servers ORDER BY position").fetchall()
    return [{"name": n, "kind": k, "host": h, "port": p} for n, k, h, p in rows]


def txn_set_board(conn, channel_id, message_id):
    conn.execute(
        "INSERT INTO boards (channel_id, message_id) VALUES (?, ?) ON CONFLICT(channel_id) DO UPDATE SET message_id = excluded.message_id",
        (channel_id, message_id),
    )
    conn.commit()


def txn_remove_board(conn, channel_id):
    removed = conn.execute("DELETE FROM boards WHERE channel_id = ?", (channel_id,)).rowcount
    conn.commit()
    return removed > 0


def txn_list_boards(conn):
    return dict(conn.execute("SELECT channel_id, message_id FROM boards").fetchall())


def players_text(status):
    if status.players is None:
        return None
    return f"{status.players}/{status.max_players}" if status.max_players is not None else str(status.players)


def server_line(status):
    """The value half of one server's row: players, map and detail when known, or why it is down."""
    if not status.up:
        return f"no answer ({status.error})" if status.error else "no answer"
    parts = []
    if players_text(status):
        parts.append(f"{players_text(status)} players")
    if status.map:
        parts.append(f"map {status.map}")
    if status.detail:
        parts.append(status.detail)
    return " - ".join(parts) or "reachable"


def server_title(server, status):
    return f"[{'up' if status.up else 'down'}] {server['name']}"


def checked_at(now):
    return now.astimezone(datetime.timezone.utc).strftime("%H:%M:%S UTC")


def status_text(rows, now):
    """Plain-text rendering, used for the on-demand reply and as the fallback when embeds are refused."""
    if not rows:
        return "**Game servers**\nno servers are configured."
    lines = [f"{server_title(server, status)} - {server_line(status)}" for server, status in rows]
    return "**Game servers**\n" + "\n".join(lines) + f"\nlast checked {checked_at(now)}"


def status_embed(rows, now):
    ups = sum(1 for _, status in rows if status.up)
    color = COLORS["all_up" if ups == len(rows) else "all_down" if ups == 0 else "mixed"]
    embed = Embed(title="Game servers", color=color, footer=f"last checked {checked_at(now)}", timestamp=now)
    embed.description = f"{ups} of {len(rows)} up" if rows else "no servers are configured."
    for server, status in rows:
        embed.add_field(server_title(server, status), server_line(status))
    return embed
