#!/usr/bin/env python3
"""bot-draw: `!draw <query>` traces an openly licensed picture into a bounded line drawing on a voice canvas; see README.md."""

import asyncio
import functools
import time

import sources
import tracer
from slimbots import ApiError, Bot, Permissions
from slimbots.http import is_rate_limited

REMOVE_BATCH = 64  # the server's most object ids in one remove op
SLOTS = 6
PER_ROW = 3
STROKE_WIDTH = 3.0
MAX_QUERY = 60
ATTEMPTS = 3
RATE_RETRIES = 4
RATE_BACKOFF = 1.5


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS drawings (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, query TEXT NOT NULL, slot INTEGER NOT NULL,
            created_at INTEGER NOT NULL, live INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS strokes (drawing_id INTEGER NOT NULL, object_id TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS strokes_by_drawing ON strokes (drawing_id);
        """
    )
    conn.commit()


bot = Bot(prefix="!", default_data_path="draw.db", store_migrate=init_db)

CANVAS_CHANNEL = bot.setting("DRAW_CHANNEL")
ORIGIN_X = bot.setting("DRAW_X", 900.0, type=float)
ORIGIN_Y = bot.setting("DRAW_Y", 600.0, type=float)
SIZE = bot.setting("DRAW_SIZE", 480.0, type=float)
MAX_STROKES = min(max(bot.setting("DRAW_MAX_STROKES", 150, type=int), 10), 300)
PACE_SECONDS = max(bot.setting("DRAW_PACE", 0.15, type=float), 0.1)
OPENVERSE_TOKEN = bot.setting("OPENVERSE_TOKEN")
GRID = 96

get = sources.http_get
canvas = None
drawing_lock = asyncio.Lock()
COOLDOWN_SECONDS = 60
# user id -> monotonic time of that member's last drawing, so one person cannot keep the pen busy.
last_draw = {}


def new_drawing(conn, user_id, query):
    taken = {r[0] for r in conn.execute("SELECT slot FROM drawings WHERE live = 1")}
    slot = next((s for s in range(SLOTS) if s not in taken), None)
    if slot is None:
        return None
    cur = conn.execute("INSERT INTO drawings (user_id, query, slot, created_at) VALUES (?, ?, ?, ?)", (user_id, query, slot, int(time.time())))
    conn.commit()
    return cur.lastrowid, slot


def add_stroke(conn, drawing_id, object_id):
    conn.execute("INSERT INTO strokes (drawing_id, object_id) VALUES (?, ?)", (drawing_id, object_id))
    conn.commit()


def latest_live(conn, user_id):
    row = conn.execute("SELECT id, query FROM drawings WHERE live = 1 AND user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
    return row


def live_drawings(conn):
    return [r[0] for r in conn.execute("SELECT id FROM drawings WHERE live = 1 ORDER BY id")]


def stroke_ids(conn, drawing_id):
    return [r[0] for r in conn.execute("SELECT object_id FROM strokes WHERE drawing_id = ? ORDER BY rowid", (drawing_id,))]


def retire(conn, drawing_id):
    conn.execute("UPDATE drawings SET live = 0 WHERE id = ?", (drawing_id,))
    conn.commit()


def corner(slot):
    return ORIGIN_X + (slot % PER_ROW) * (SIZE + 40), ORIGIN_Y + (slot // PER_ROW) * (SIZE + 40)


async def place_with_retry(**kwargs):
    for attempt in range(RATE_RETRIES):
        try:
            return await canvas.place("stroke", **kwargs)
        except ApiError as err:
            if not is_rate_limited(err) or attempt == RATE_RETRIES - 1:
                raise
            await asyncio.sleep(RATE_BACKOFF * (attempt + 1))


async def draw_pieces(drawing_id, slot, pieces, width, height):
    """Places each polyline as one stroke object, paced under the canvas write limit; returns how many went down."""
    scale = SIZE / max(width, height)
    left, top = corner(slot)
    done = 0
    for piece in pieces:
        low_x, low_y = min(p[0] for p in piece), min(p[1] for p in piece)
        relative = [round(v * scale, 1) for p in piece for v in ((p[0] - low_x), (p[1] - low_y))]
        placed = await place_with_retry(
            x=left + low_x * scale, y=top + low_y * scale, w=max(max(p[0] for p in piece) - low_x, 0.5) * scale,
            h=max(max(p[1] for p in piece) - low_y, 0.5) * scale, props={"points": relative, "width": STROKE_WIDTH},
        )
        await bot.store.run(add_stroke, drawing_id, placed["id"])
        done += 1
        await asyncio.sleep(PACE_SECONDS)
    return done


async def find_drawable(query):
    """(candidate, pieces, width, height) for the first search result that traces to something, else a sentence."""
    found = await asyncio.to_thread(sources.search, functools.partial(get, token=OPENVERSE_TOKEN), query)
    if not found:
        return "no openly licensed picture matched that; try a simpler word."
    for candidate in found[:ATTEMPTS]:
        try:
            data = await asyncio.to_thread(sources.fetch_picture, get, candidate["url"])
            grid, width, height = await asyncio.to_thread(sources.decode_gray, data, GRID)
        except sources.SourceError as err:
            if "Pillow" in str(err):
                return str(err) + "."
            continue
        pieces = tracer.trace(grid, MAX_STROKES)
        if pieces:
            return candidate, pieces, width, height
    return "I could not turn any of the matching pictures into a drawing; try another word."


def credit(candidate, query, count):
    return f"drew \"{query}\" with {count} strokes from \"{candidate['title']}\" by {candidate['creator']} ({candidate['license']}) {candidate['landing']}".strip()


async def remove_strokes(object_ids):
    for start in range(0, len(object_ids), REMOVE_BATCH):
        await canvas.remove(object_ids[start : start + REMOVE_BATCH])


@bot.group(name="draw", help="`<query>` draws a picture, `undo` removes your latest, `clear` removes them all")
async def draw(ctx, query: str = ""):
    query = query.strip()
    if not query:
        await ctx.reply_ephemeral(f"`{bot.prefix}draw <what to draw>`, `{bot.prefix}draw undo`, `{bot.prefix}draw clear`", public_fallback=True)
        return
    if canvas is None:
        await ctx.reply_ephemeral("the canvas channel is not set up; the operator sets DRAW_CHANNEL to a voice channel.", public_fallback=True)
        return
    if len(query) > MAX_QUERY:
        await ctx.reply_ephemeral(f"keep it under {MAX_QUERY} characters.", public_fallback=True)
        return
    if drawing_lock.locked():
        await ctx.reply_ephemeral("I am still drawing; try again in a moment.", public_fallback=True)
        return
    wait = COOLDOWN_SECONDS - (time.monotonic() - last_draw.get(ctx.author.id, -COOLDOWN_SECONDS))
    if wait > 0:
        await ctx.reply_ephemeral(f"you can draw again in {int(wait) + 1}s.", public_fallback=True)
        return
    last_draw[ctx.author.id] = time.monotonic()
    async with drawing_lock, ctx.typing():
        await _draw(ctx, query)


async def _draw(ctx, query):
    try:
        outcome = await find_drawable(query)
    except sources.SourceError as err:
        await ctx.reply_ephemeral(f"{err}.", public_fallback=True)
        return
    if isinstance(outcome, str):
        await ctx.reply_ephemeral(outcome, public_fallback=True)
        return
    candidate, pieces, width, height = outcome
    started = await bot.store.run(new_drawing, ctx.author.id, query)
    if started is None:
        await ctx.reply_ephemeral(f"the drawing area holds {SLOTS} drawings; `{bot.prefix}draw undo` or `clear` makes room.", public_fallback=True)
        return
    drawing_id, slot = started
    try:
        done = await draw_pieces(drawing_id, slot, pieces, width, height)
    except ApiError as err:
        await ctx.reply_ephemeral(f"the canvas stopped me after part of it (http {err.status}); `{bot.prefix}draw undo` removes what is there.", public_fallback=True)
        return
    await ctx.reply(credit(candidate, query.replace("@", " at "), done))


@draw.command(name="undo", help="Remove your latest drawing, all of it at once")
async def draw_undo(ctx):
    row = await bot.store.run(latest_live, ctx.author.id)
    if row is None or canvas is None:
        await ctx.reply_ephemeral("you have no drawing to undo.", public_fallback=True)
        return
    await remove_strokes(await bot.store.run(stroke_ids, row[0]))
    await bot.store.run(retire, row[0])
    await ctx.reply_ephemeral(f"removed your drawing of \"{row[1]}\".", public_fallback=True)


@draw.command(name="clear", help="Remove every drawing this bot made", requires=Permissions.MANAGE_CANVAS, requires_message="only someone with Manage Canvas can clear the drawings.")
async def draw_clear(ctx):
    drawings = await bot.store.run(live_drawings)
    for drawing_id in drawings:
        await remove_strokes(await bot.store.run(stroke_ids, drawing_id))
        await bot.store.run(retire, drawing_id)
    await ctx.reply_ephemeral(f"removed {len(drawings)} drawing{'s' if len(drawings) != 1 else ''}.", public_fallback=True)


async def _refuse_to_start(err):
    """A handler's own exception is swallowed; one out of a background task stops the bot."""
    raise err


@bot.event
async def on_connect():
    global canvas
    wanted = CANVAS_CHANNEL or bot.channel
    channel = bot.space.get_channel(wanted) if wanted else None
    if channel is None or channel.kind != "voice":
        bot.background(_refuse_to_start(RuntimeError("set DRAW_CHANNEL to the voice channel whose canvas the drawings go on")), name="draw-config")
        return
    canvas = bot.canvas(channel.id)


if __name__ == "__main__":
    bot.run_cli()
