#!/usr/bin/env python3
"""bot-uptime-relay: posts Uptime Kuma, Grafana and Healthchecks alerts with Acknowledge and Silence buttons; see README.md."""

import asyncio
import functools
import sys
import time
import uuid

import relay
from slimbots import ApiError, Bot, Button, Permissions, rows
from slimbots.http import is_not_found, is_token_revoked
from slimbots.migrations import ensure_columns
from slimbots.text import escape_mentions, format_duration


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, source TEXT NOT NULL, service TEXT NOT NULL,
            message TEXT NOT NULL, url TEXT, message_id TEXT NOT NULL, since INTEGER NOT NULL,
            repeats INTEGER NOT NULL DEFAULT 0, acked_by TEXT
        );
        CREATE TABLE IF NOT EXISTS silences (key TEXT PRIMARY KEY, until INTEGER NOT NULL, by TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS pending (
            id INTEGER PRIMARY KEY, key TEXT NOT NULL, source TEXT NOT NULL, service TEXT NOT NULL, down INTEGER NOT NULL,
            message TEXT NOT NULL, url TEXT, ident TEXT, former TEXT, at INTEGER NOT NULL
        );
        """
    )
    ensure_columns(conn, "alerts", {"posted": "INTEGER NOT NULL DEFAULT 1"})


bot = Bot(prefix="!", require_channels=True, default_data_path="uptime-relay.db", store_migrate=init_db)

SECRET = bot.setting("UPTIME_RELAY_SECRET", required=True)
HOST = bot.setting("UPTIME_RELAY_HOST", "0.0.0.0")
PORT = bot.setting("UPTIME_RELAY_PORT", 8790, type=int)
SILENCE_SECONDS = max(60, bot.setting("UPTIME_RELAY_SILENCE_MINUTES", 60, type=int) * 60)
OPERATOR_ROLE = bot.setting("UPTIME_RELAY_OPERATOR_ROLE", "")
RETRY_SECONDS = 30
HOLD_SECONDS = 24 * 3600

COLUMNS = "id, key, source, service, message, url, message_id, since, repeats, acked_by, posted"
PENDING_COLUMNS = "id, key, source, service, down, message, url, ident, former, at"


def _row(conn, where, value):
    found = conn.execute(f"SELECT {COLUMNS} FROM alerts WHERE {where} = ?", (value,)).fetchone()
    return dict(zip(COLUMNS.split(", "), found)) if found else None


def open_alert(conn, key):
    return _row(conn, "key", key)


def alert_by_id(conn, alert_id):
    return _row(conn, "id", alert_id)


def silence_of(conn, key, now):
    found = conn.execute("SELECT until, by FROM silences WHERE key = ? AND until > ?", (key, now)).fetchone()
    return {"until": found[0], "by": found[1]} if found else None


def insert_alert(conn, alert, message_id, now):
    conn.execute(
        "INSERT INTO alerts (key, source, service, message, url, message_id, since, posted) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
        (alert.key, alert.source, alert.service, alert.message, alert.url, message_id, now),
    )
    conn.commit()
    return open_alert(conn, alert.key)


def mark_posted(conn, key):
    conn.execute("UPDATE alerts SET posted = 1 WHERE key = ?", (key,))
    conn.commit()


def bump_alert(conn, key, message):
    conn.execute("UPDATE alerts SET repeats = repeats + 1, message = ? WHERE key = ?", (message, key))
    conn.commit()


def ack_alert(conn, alert_id, by):
    conn.execute("UPDATE alerts SET acked_by = ? WHERE id = ?", (by, alert_id))
    conn.commit()
    return alert_by_id(conn, alert_id)


def set_silence(conn, key, until, by):
    conn.execute("INSERT INTO silences (key, until, by) VALUES (?, ?, ?) ON CONFLICT(key) DO UPDATE SET until = excluded.until, by = excluded.by", (key, until, by))
    conn.commit()


def close_alert(conn, key):
    conn.execute("DELETE FROM alerts WHERE key = ?", (key,))
    conn.commit()


def close_unposted(conn, key):
    conn.execute("DELETE FROM alerts WHERE key = ? AND posted = 0", (key,))
    conn.commit()


def move_alert(conn, key, message_id):
    conn.execute("UPDATE alerts SET message_id = ? WHERE key = ?", (message_id, key))
    conn.commit()
    return open_alert(conn, key)


def hold_alerts(conn, alerts, now):
    """Stores `alerts` until they are delivered and returns their pending ids; a copy of a service's last held alert is not stored twice."""
    held = []
    for alert in alerts:
        last = conn.execute("SELECT id, down, message FROM pending WHERE key = ? ORDER BY id DESC LIMIT 1", (alert.key,)).fetchone()
        if last and (bool(last[1]), last[2]) == (alert.down, alert.message):
            held.append(last[0])
            continue
        cursor = conn.execute(
            "INSERT INTO pending (key, source, service, down, message, url, ident, former, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (alert.key, alert.source, alert.service, int(alert.down), alert.message, alert.url, alert.ident, alert.former, now),
        )
        held.append(cursor.lastrowid)
    conn.commit()
    return held


def held_alerts(conn):
    found = conn.execute(f"SELECT {PENDING_COLUMNS} FROM pending ORDER BY id").fetchall()
    return [dict(zip(PENDING_COLUMNS.split(", "), row)) for row in found]


def release_alert(conn, pending_id):
    conn.execute("DELETE FROM pending WHERE id = ?", (pending_id,))
    conn.commit()


def held_alert(entry):
    return relay.Alert(
        entry["source"], entry["service"], bool(entry["down"]), entry["message"], entry["url"], ident=entry["ident"], former=entry["former"],
    )


def clock(epoch):
    return time.strftime("%H:%M UTC", time.gmtime(epoch))


def render(row, silence=None):
    lines = [f"DOWN - {row['service']} ({row['source']})"]
    if row["message"]:
        lines.append(row["message"])
    meta = [f"since {clock(row['since'])}"]
    if row["repeats"]:
        meta.append(f"{row['repeats'] + 1} alerts")
    if row["url"]:
        meta.append(row["url"])
    lines.append(" - ".join(meta))
    if row["acked_by"]:
        lines.append(f"acknowledged by {row['acked_by']}")
    if silence:
        lines.append(f"silenced by {silence['by']} until {clock(silence['until'])}")
    return escape_mentions("\n".join(lines))


def render_resolved(row, now):
    return escape_mentions(f"RESOLVED - {row['service']} ({row['source']}) was down for {format_duration(now - row['since'], 'minutes')}.")


def buttons(row, silence=None):
    found = []
    if not row["acked_by"]:
        found.append(Button("Acknowledge", f"ur:ack:{row['id']}", style="primary"))
    if not silence:
        found.append(Button("Silence 1h" if SILENCE_SECONDS == 3600 else "Silence", f"ur:silence:{row['id']}"))
    return rows(found) if found else []


async def _tolerate(row, call):
    """Awaits an edit of the alert's post; False when the post is gone, and any other failure is logged and swallowed."""
    try:
        await call
    except ApiError as err:
        if is_token_revoked(err):
            raise
        if is_not_found(err):
            return False
        print(f"could not edit the alert for {row['key']}: {err}", file=sys.stderr)
    return True


async def _edit(row, content):
    return await _tolerate(row, bot.client.edit_message(bot.channel, row["message_id"], content))


async def _gone(call):
    """Awaits a change to the alert's post; True when the post is gone, and any other failure is raised so the alert is retried."""
    try:
        await call
    except ApiError as err:
        if is_not_found(err):
            return True
        raise
    return False


async def _post(row):
    """Sends the alert under its stored id, so a retry after an unclear failure cannot post it twice."""
    await bot.client.send(bot.channel, render(row), message_id=row["message_id"], components=buttons(row))
    await bot.store.run(mark_posted, row["key"])


async def _post_again(row):
    """Replaces a post that was deleted, by a moderator or by retention, so the outage is still announced."""
    message_id = str(uuid.uuid4())
    await bot.client.send(bot.channel, render(row), message_id=message_id, components=buttons(row))
    await bot.store.run(move_alert, row["key"], message_id)


async def _resolve(row, now):
    text = render_resolved(row, now)
    if await _gone(bot.client.edit_message(bot.channel, row["message_id"], text)):
        await bot.client.send(bot.channel, text, message_id=str(uuid.uuid5(uuid.UUID(row["message_id"]), "resolved")))
    else:
        await _gone(bot.client.edit_components(bot.channel, row["message_id"], []))
    await bot.store.run(close_alert, row["key"])


async def _open_row(alert):
    """The alert's open row, or one opened under the key it had before alerts carried an `ident`."""
    row = await bot.store.run(open_alert, alert.key)
    if row is None and alert.former_key:
        row = await bot.store.run(open_alert, alert.former_key)
    return row


async def handle(alert, now=None):
    now = int(now if now is not None else time.time())
    row = await _open_row(alert)
    if not alert.down:
        if row:
            await _resolve(row, now)
        return
    if row and not row["posted"]:
        await _post(row)
        return
    silence = await bot.store.run(silence_of, row["key"] if row else alert.key, now)
    if row:
        row = {**row, "repeats": row["repeats"] + 1, "message": alert.message}
        if not await _edit(row, render(row, silence)) and not silence:
            await _post_again(row)
        await bot.store.run(bump_alert, row["key"], alert.message)
        return
    if silence:
        return
    # Stored first, so the buttons can name the row; it stays unposted until the send goes through.
    await _post(await bot.store.run(insert_alert, alert, str(uuid.uuid4()), now))


_handling = asyncio.Lock()


async def handle_all(alerts):
    """Holds the alerts, then delivers everything held, one at a time; False when one of these alerts was not delivered."""
    async with _handling:
        held = await bot.store.run(hold_alerts, alerts, int(time.time()))
        undelivered = await _deliver_held()
    return not undelivered.intersection(held)


@bot.every(RETRY_SECONDS, name="uptime-relay-retry", delay_first=True)
async def retry_pending():
    async with _handling:
        await _deliver_held()


async def _deliver_held():
    """Delivers held alerts oldest first and returns the ids it could not deliver.
    A failure holds back that service's later alerts so they never overtake it, and an unreachable server ends the pass."""
    blocked, undelivered, unreachable = set(), set(), False
    now = int(time.time())
    for entry in await bot.store.run(held_alerts):
        if unreachable or entry["key"] in blocked:
            undelivered.add(entry["id"])
            continue
        if now - entry["at"] > HOLD_SECONDS:
            print(f"gave up on the alert for {entry['key']} held since {clock(entry['at'])}", file=sys.stderr)
            await bot.store.run(close_unposted, entry["key"])
            outcome = "dropped"
        else:
            outcome = await _deliver_one(entry)
        if outcome in ("sent", "dropped"):
            await bot.store.run(release_alert, entry["id"])
        if outcome != "sent":
            undelivered.add(entry["id"])
        if outcome in ("unreachable", "refused"):
            blocked.add(entry["key"])
            unreachable = outcome == "unreachable"
    return undelivered


async def _deliver_one(entry):
    """'sent'; 'unreachable' or 'refused' to retry later; 'dropped' for a failure no retry can fix."""
    try:
        await handle(held_alert(entry), now=entry["at"])
    except ApiError as err:
        if is_token_revoked(err):
            raise
        print(f"could not deliver the alert for {entry['key']}, will retry: {err}", file=sys.stderr)
        return "unreachable" if err.status is None or err.status >= 500 else "refused"
    except Exception as err:
        print(f"{type(err).__name__} handling {entry['key']}, dropped: {err}", file=sys.stderr)
        return "dropped"
    return "sent"


async def is_operator(user_id):
    """Manage Server, or the role UPTIME_RELAY_OPERATOR_ROLE names."""
    try:
        member = await bot.space.find_member(user_id)
    except ApiError:
        return False
    if member is None:
        return False
    role = bot.space.get_role(OPERATOR_ROLE) if OPERATOR_ROLE else None
    return member.has_permission(Permissions.MANAGE_SERVER) or (role is not None and role.id in member.role_ids)


@bot.button(prefix="ur:")
async def on_press(interaction):
    _, action, raw_id = (interaction.custom_id.split(":") + ["", ""])[:3]
    if action not in ("ack", "silence"):
        await interaction.ack()
        return
    if not await is_operator(interaction.user_id):
        await interaction.reply_ephemeral("only someone with Manage Server or the operator role can acknowledge or silence alerts.")
        return
    row = await bot.store.run(alert_by_id, int(raw_id)) if raw_id.isdigit() else None
    if row is None:
        await interaction.reply_ephemeral("that alert has already resolved.")
        return
    who = interaction.user_display_name or "someone"
    now = int(time.time())
    if action == "ack":
        row = await bot.store.run(ack_alert, row["id"], who)
    else:
        await bot.store.run(set_silence, row["key"], now + SILENCE_SECONDS, who)
    silence = await bot.store.run(silence_of, row["key"], now)
    await _edit(row, render(row, silence))
    await interaction.edit_components(buttons(row, silence))


async def serve():
    handler = functools.partial(relay.handle_connection, secret=SECRET, on_alerts=handle_all)
    server = await asyncio.start_server(handler, HOST, PORT)
    print(f"uptime-relay listening on {HOST}:{PORT}", file=sys.stderr)
    async with server:
        await server.serve_forever()


started = []


@bot.event
async def on_connect():
    if started:
        return
    started.append(True)
    bot.background(serve(), name="uptime-relay-http")


if __name__ == "__main__":
    bot.run_cli()
