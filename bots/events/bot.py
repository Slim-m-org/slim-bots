#!/usr/bin/env python3
"""bot-events: `!event "movie night" fri 8pm` posts Going/Not going buttons and reminds the people who are going; see README.md."""

import time
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import planning
from slimbots import ApiError, Bot, Button, Permissions, rows
from slimbots.http import is_token_revoked

MAX_OPEN_EVENTS = 10
MAX_MENTIONS = 25
TICK_SECONDS = 20


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id TEXT NOT NULL, message_id TEXT, title TEXT NOT NULL,
            start_ts INTEGER NOT NULL, creator_id TEXT NOT NULL, state INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS rsvps (event_id INTEGER NOT NULL, user_id TEXT NOT NULL, going INTEGER NOT NULL, PRIMARY KEY (event_id, user_id));
        """
    )
    conn.commit()


bot = Bot(prefix="!", default_data_path="events.db", store_migrate=init_db)

TZ_NAME = bot.setting("EVENTS_TZ", "UTC")
REMIND_MINUTES = max(bot.setting("EVENTS_REMIND_MINUTES", 15, type=int), 0)

# Event states: 0 open, 1 reminded, 2 started, 3 cancelled.
OPEN, REMINDED, STARTED, CANCELLED = 0, 1, 2, 3


def zone():
    try:
        return ZoneInfo(TZ_NAME)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def when_text(start_ts):
    return datetime.fromtimestamp(start_ts, zone()).strftime("%a %Y-%m-%d %H:%M %Z")


def add_event(conn, channel_id, title, start_ts, creator_id):
    cur = conn.execute("INSERT INTO events (channel_id, title, start_ts, creator_id) VALUES (?, ?, ?, ?)", (channel_id, title, start_ts, creator_id))
    conn.commit()
    return cur.lastrowid


def set_message(conn, event_id, message_id):
    conn.execute("UPDATE events SET message_id = ? WHERE id = ?", (message_id, event_id))
    conn.commit()


def get_event(conn, event_id):
    return conn.execute("SELECT id, channel_id, message_id, title, start_ts, creator_id, state FROM events WHERE id = ?", (event_id,)).fetchone()


def open_events(conn):
    return conn.execute("SELECT id, title, start_ts FROM events WHERE state < ? ORDER BY start_ts", (STARTED,)).fetchall()


def set_state(conn, event_id, state):
    conn.execute("UPDATE events SET state = ? WHERE id = ?", (state, event_id))
    conn.commit()


def record_rsvp(conn, event_id, user_id, going):
    conn.execute(
        "INSERT INTO rsvps (event_id, user_id, going) VALUES (?, ?, ?) ON CONFLICT (event_id, user_id) DO UPDATE SET going = excluded.going",
        (event_id, user_id, going),
    )
    conn.commit()


def going_users(conn, event_id):
    return [r[0] for r in conn.execute("SELECT user_id FROM rsvps WHERE event_id = ? AND going = 1 ORDER BY rowid", (event_id,))]


def due_events(conn, now):
    return conn.execute("SELECT id, channel_id, title, start_ts, state FROM events WHERE state < ? AND start_ts - ? <= ?", (STARTED, REMIND_MINUTES * 60, now)).fetchall()


def event_text(event_id, title, start_ts, going):
    return f"{title} - {when_text(start_ts)}\n{going} going. Press a button to say if you are coming (event #{event_id})."


def buttons(event_id):
    return rows([Button("Going", f"event:{event_id}:yes", style="primary"), Button("Not going", f"event:{event_id}:no", style="secondary")])


async def names_for(user_ids):
    members = [await bot.space.find_member(u) for u in user_ids[:MAX_MENTIONS]]
    shown = " ".join(m.mention() for m in members if m is not None)
    return shown + (f" and {len(user_ids) - MAX_MENTIONS} more" if len(user_ids) > MAX_MENTIONS else "")


@bot.command(name="event", help='Plan an event: `"movie night" fri 8pm`, or `list`, or `cancel <n>`', usage='"<title>" <day> <time> | list | cancel <n>')
async def event(ctx, args: str = ""):
    word, _, rest = args.partition(" ")
    if word.lower() == "list":
        await _list(ctx)
    elif word.lower() == "cancel":
        await _cancel(ctx, rest.strip())
    else:
        await _create(ctx, args)


async def _create(ctx, args):
    try:
        title, start = planning.parse_event(args, datetime.now(zone()))
    except ValueError as err:
        await ctx.reply_ephemeral(f"{err} Example: `{bot.prefix}event \"movie night\" fri 8pm` ({TZ_NAME} time).", public_fallback=True)
        return
    if len(await bot.store.run(open_events)) >= MAX_OPEN_EVENTS:
        await ctx.reply_ephemeral(f"there are already {MAX_OPEN_EVENTS} open events; cancel one first.", public_fallback=True)
        return
    start_ts = int(start.timestamp())
    event_id = await bot.store.run(add_event, ctx.channel_id, title, start_ts, ctx.author.id)
    sent = await bot.client.send(ctx.channel_id, event_text(event_id, title, start_ts, 0), components=buttons(event_id))
    await bot.store.run(set_message, event_id, sent.id)


async def _list(ctx):
    events = await bot.store.run(open_events)
    if not events:
        await ctx.reply_ephemeral("no events are planned.", public_fallback=True)
        return
    await ctx.reply_ephemeral("\n".join(f"- #{i} {t} - {when_text(s)}" for i, t, s in events), public_fallback=True)


async def _cancel(ctx, number):
    row = await bot.store.run(get_event, int(number)) if number.isdigit() else None
    if row is None or row[6] >= STARTED:
        await ctx.reply_ephemeral("there is no open event with that number; `list` shows them.", public_fallback=True)
        return
    if row[5] != ctx.author.id and not ctx.author.has_permission(Permissions.MANAGE_MESSAGES):
        await ctx.reply_ephemeral("only the person who planned it, or someone with Manage Messages, can cancel it.", public_fallback=True)
        return
    await bot.store.run(set_state, row[0], CANCELLED)
    await _retire_message(row, f"{row[3]} - cancelled.")
    await ctx.reply_ephemeral(f"cancelled #{row[0]}.", public_fallback=True)


async def _retire_message(row, text):
    """Rewrites the event post without buttons; a deleted post is not worth failing over."""
    if not row[2]:
        return
    try:
        await bot.client.edit_message(row[1], row[2], text)
        await bot.client.edit_components(row[1], row[2], [])
    except ApiError as err:
        if is_token_revoked(err):
            raise


@bot.button(prefix="event:")
async def on_rsvp(interaction):
    _, event_id, answer = interaction.custom_id.split(":")
    row = await bot.store.run(get_event, int(event_id))
    if row is None or row[6] >= STARTED or answer not in ("yes", "no"):
        await interaction.reply_ephemeral("this event is over.")
        return
    await bot.store.run(record_rsvp, row[0], interaction.user_id, 1 if answer == "yes" else 0)
    count = len(await bot.store.run(going_users, row[0]))
    await bot.client.edit_message(row[1], row[2], event_text(row[0], row[3], row[4], count))
    await interaction.reply_ephemeral("you are going." if answer == "yes" else "you are not going.")


async def announce(row):
    event_id, channel_id, title, start_ts, state = row
    starting = time.time() >= start_ts
    users = await bot.store.run(going_users, event_id)
    await bot.store.run(set_state, event_id, STARTED if starting else REMINDED)
    if not users:
        return
    who = await names_for(users)
    lead = "is starting now" if starting else f"starts at {when_text(start_ts)}"
    await bot.client.send(channel_id, f"{title} {lead}. {who}")


@bot.every(TICK_SECONDS, name="events", on="ready")
async def tick():
    for row in await bot.store.run(due_events, int(time.time())):
        if row[4] == REMINDED and time.time() < row[3]:
            continue
        await announce(row)


if __name__ == "__main__":
    bot.run_cli()
