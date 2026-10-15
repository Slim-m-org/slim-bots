#!/usr/bin/env python3
"""bot-uptime-relay: posts Uptime Kuma, Grafana and Healthchecks alerts with Acknowledge and Silence buttons; see README.md."""

import asyncio
import functools
import sys
import time
import uuid

import relay
from slimbots import ApiError, Bot, Button, rows
from slimbots.http import is_token_revoked


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY, key TEXT NOT NULL UNIQUE, source TEXT NOT NULL, service TEXT NOT NULL,
            message TEXT NOT NULL, url TEXT, message_id TEXT NOT NULL, since INTEGER NOT NULL,
            repeats INTEGER NOT NULL DEFAULT 0, acked_by TEXT
        );
        CREATE TABLE IF NOT EXISTS silences (key TEXT PRIMARY KEY, until INTEGER NOT NULL, by TEXT NOT NULL);
        """
    )
    conn.commit()


bot = Bot(prefix="!", require_channels=True, default_data_path="uptime-relay.db", store_migrate=init_db)

SECRET = bot.setting("UPTIME_RELAY_SECRET", required=True)
HOST = bot.setting("UPTIME_RELAY_HOST", "0.0.0.0")
PORT = bot.setting("UPTIME_RELAY_PORT", 8790, type=int)
SILENCE_SECONDS = max(60, bot.setting("UPTIME_RELAY_SILENCE_MINUTES", 60, type=int) * 60)

COLUMNS = "id, key, source, service, message, url, message_id, since, repeats, acked_by"


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
        "INSERT INTO alerts (key, source, service, message, url, message_id, since) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (alert.key, alert.source, alert.service, alert.message, alert.url, message_id, now),
    )
    conn.commit()
    return open_alert(conn, alert.key)


def bump_alert(conn, key, message):
    conn.execute("UPDATE alerts SET repeats = repeats + 1, message = ? WHERE key = ?", (message, key))
    conn.commit()
    return open_alert(conn, key)


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


def clock(epoch):
    return time.strftime("%H:%M UTC", time.gmtime(epoch))


def duration(seconds):
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


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
    return "\n".join(lines)


def render_resolved(row, now):
    return f"RESOLVED - {row['service']} ({row['source']}) was down for {duration(now - row['since'])}."


def buttons(row, silence=None):
    found = []
    if not row["acked_by"]:
        found.append(Button("Acknowledge", f"ur:ack:{row['id']}", style="primary"))
    if not silence:
        found.append(Button("Silence 1h" if SILENCE_SECONDS == 3600 else "Silence", f"ur:silence:{row['id']}"))
    return rows(found) if found else []


async def _edit(row, content):
    try:
        await bot.client.edit_message(bot.channel, row["message_id"], content)
    except ApiError as err:
        if is_token_revoked(err):
            raise
        print(f"could not edit the alert for {row['key']}: {err}", file=sys.stderr)


async def handle(alert, now=None):
    now = int(now if now is not None else time.time())
    row = await bot.store.run(open_alert, alert.key)
    if not alert.down:
        if row:
            await _edit(row, render_resolved(row, now))
            await bot.client.edit_components(bot.channel, row["message_id"], [])
            await bot.store.run(close_alert, alert.key)
        return
    silence = await bot.store.run(silence_of, alert.key, now)
    if row:
        row = await bot.store.run(bump_alert, alert.key, alert.message)
        await _edit(row, render(row, silence))
        return
    if silence:
        return
    # Stored first, so the buttons can name the row; dropped again if the post fails.
    row = await bot.store.run(insert_alert, alert, str(uuid.uuid4()), now)
    try:
        await bot.client.send(bot.channel, render(row), message_id=row["message_id"], components=buttons(row))
    except ApiError:
        await bot.store.run(close_alert, alert.key)
        raise


_handling = asyncio.Lock()


async def handle_all(alerts):
    """One alert at a time, so two webhooks for one service cannot both open it."""
    async with _handling:
        for alert in alerts:
            try:
                await handle(alert)
            except ApiError as err:
                if is_token_revoked(err):
                    raise
                print(f"could not post the alert for {alert.key}: {err}", file=sys.stderr)
            except Exception as err:
                print(f"{type(err).__name__} handling {alert.key}: {err}", file=sys.stderr)


@bot.button(prefix="ur:")
async def on_press(interaction):
    _, action, raw_id = (interaction.custom_id.split(":") + ["", ""])[:3]
    row = await bot.store.run(alert_by_id, int(raw_id)) if raw_id.isdigit() else None
    if row is None:
        await interaction.reply_ephemeral("that alert has already resolved.")
        return
    who = interaction.user_display_name or "someone"
    now = int(time.time())
    if action == "ack":
        row = await bot.store.run(ack_alert, row["id"], who)
    elif action == "silence":
        await bot.store.run(set_silence, row["key"], now + SILENCE_SECONDS, who)
    else:
        await interaction.ack()
        return
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
