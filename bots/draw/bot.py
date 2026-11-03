#!/usr/bin/env python3
"""bot-draw: `!draw <prompt>` has a model draw on the canvas of the call you are in; `!draw trace <query>` traces a picture; see README.md."""

import asyncio
import functools
import time

import ai
import plan
import screenshot
import sources
import tracer
import where
from slimbots import ApiError, Bot, Permissions
from slimbots.http import is_rate_limited

REMOVE_BATCH = 64  # the server's most object ids in one remove op
SLOTS = 6
PER_ROW = 3
STROKE_WIDTH = 3.0
MAX_QUERY = 60
MAX_PROMPT = 200
TILE_BYTES = 2 * 1024 * 1024
TILE_SECONDS = 10
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
        CREATE TABLE IF NOT EXISTS ai_usage (day TEXT PRIMARY KEY, calls INTEGER NOT NULL);
        """
    )
    if "channel_id" not in {row[1] for row in conn.execute("PRAGMA table_info(drawings)")}:
        conn.execute("ALTER TABLE drawings ADD COLUMN channel_id TEXT NOT NULL DEFAULT ''")
    conn.commit()


bot = Bot(prefix="!", default_data_path="draw.db", store_migrate=init_db, listen_voice_chats=True)

ORIGIN_X = bot.setting("DRAW_X", 900.0, type=float)
ORIGIN_Y = bot.setting("DRAW_Y", 600.0, type=float)
SIZE = bot.setting("DRAW_SIZE", 480.0, type=float)
MAX_STROKES = min(max(bot.setting("DRAW_MAX_STROKES", 150, type=int), 10), 300)
PACE_SECONDS = max(bot.setting("DRAW_PACE", 0.15, type=float), 0.1)
OPENVERSE_TOKEN = bot.setting("OPENVERSE_TOKEN")
VIEW_X = bot.setting("DRAW_VIEW_X", 0.0, type=float)
VIEW_Y = bot.setting("DRAW_VIEW_Y", 0.0, type=float)
AI_COOLDOWN = bot.setting("DRAW_AI_COOLDOWN", 120, type=int)
AI_DAILY_CAP = bot.setting("DRAW_AI_DAILY_CAP", 100, type=int)
AI_MAX_TOKENS = min(max(bot.setting("DRAW_AI_MAX_TOKENS", 4096, type=int), 512), 16000)
GRID = 96

get = sources.http_get
planner = ai.make_planner(
    api_key=bot.setting("ANTHROPIC_API_KEY"), model=bot.setting("DRAW_MODEL", "claude-sonnet-4-6"), max_tokens=AI_MAX_TOKENS,
    local_url=bot.setting("DRAW_LLM_URL"), local_model=bot.setting("DRAW_LLM_MODEL"), local_key=bot.setting("DRAW_LLM_KEY") or "",
)
COOLDOWN_SECONDS = 60
# user id -> monotonic time of that member's last drawing, so one person cannot keep the pen busy.
last_draw = {}
# Channels with a drawing in progress: one at a time per canvas.
busy = set()


def new_drawing(conn, user_id, query, channel_id, traced):
    """(drawing id, slot) or None when a traced drawing finds the channel's grid full; a model drawing takes no slot."""
    slot = -1
    if traced:
        taken = {r[0] for r in conn.execute("SELECT slot FROM drawings WHERE live = 1 AND channel_id = ?", (channel_id,))}
        slot = next((s for s in range(SLOTS) if s not in taken), None)
        if slot is None:
            return None
    cur = conn.execute(
        "INSERT INTO drawings (user_id, query, slot, created_at, channel_id) VALUES (?, ?, ?, ?, ?)", (user_id, query, slot, int(time.time()), channel_id)
    )
    conn.commit()
    return cur.lastrowid, slot


def add_stroke(conn, drawing_id, object_id):
    conn.execute("INSERT INTO strokes (drawing_id, object_id) VALUES (?, ?)", (drawing_id, object_id))
    conn.commit()


def latest_live(conn, user_id):
    return conn.execute("SELECT id, query, channel_id FROM drawings WHERE live = 1 AND user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()


def live_drawings(conn, channel_id):
    return [r[0] for r in conn.execute("SELECT id FROM drawings WHERE live = 1 AND channel_id = ? ORDER BY id", (channel_id,))]


def stroke_ids(conn, drawing_id):
    return [r[0] for r in conn.execute("SELECT object_id FROM strokes WHERE drawing_id = ? ORDER BY rowid", (drawing_id,))]


def retire(conn, drawing_id):
    conn.execute("UPDATE drawings SET live = 0 WHERE id = ?", (drawing_id,))
    conn.commit()


def take_ai_call(conn, day, cap):
    """Counts one model call against today's cap; False when the cap is spent."""
    used = conn.execute("SELECT calls FROM ai_usage WHERE day = ?", (day,)).fetchone()
    if used is not None and used[0] >= cap:
        return False
    conn.execute("INSERT INTO ai_usage (day, calls) VALUES (?, 1) ON CONFLICT (day) DO UPDATE SET calls = calls + 1", (day,))
    conn.commit()
    return True


def corner(slot):
    return ORIGIN_X + (slot % PER_ROW) * (SIZE + 40), ORIGIN_Y + (slot // PER_ROW) * (SIZE + 40)


async def place_with_retry(canvas, planned):
    for attempt in range(RATE_RETRIES):
        try:
            return await canvas.place(planned.kind, x=planned.x, y=planned.y, w=planned.w, h=planned.h, props=planned.props)
        except ApiError as err:
            if not is_rate_limited(err) or attempt == RATE_RETRIES - 1:
                raise
            await asyncio.sleep(RATE_BACKOFF * (attempt + 1))


async def place_all(canvas, drawing_id, objects):
    """Places each object paced under the canvas write limit, remembering its id for undo; returns how many went down."""
    done = 0
    for planned in objects:
        placed = await place_with_retry(canvas, planned)
        await bot.store.run(add_stroke, drawing_id, placed["id"])
        done += 1
        await asyncio.sleep(PACE_SECONDS)
    return done


def pieces_to_objects(slot, pieces, width, height):
    scale = SIZE / max(width, height)
    left, top = corner(slot)
    objects = []
    for piece in pieces:
        low_x, low_y = min(p[0] for p in piece), min(p[1] for p in piece)
        relative = [round(v * scale, 1) for p in piece for v in ((p[0] - low_x), (p[1] - low_y))]
        box_w = max(max(p[0] for p in piece) - low_x, 0.5) * scale
        box_h = max(max(p[1] for p in piece) - low_y, 0.5) * scale
        objects.append(plan.Planned("stroke", left + low_x * scale, top + low_y * scale, box_w, box_h, {"points": relative, "width": STROKE_WIDTH}))
    return objects


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


async def remove_strokes(canvas, object_ids):
    for start in range(0, len(object_ids), REMOVE_BATCH):
        await canvas.remove(object_ids[start : start + REMOVE_BATCH])


def defuse(text):
    return text.replace("@", " at ").replace("<", " ").replace(">", " ")


async def fetch_tile(attachment_id):
    """An image tile's bytes for the screenshot (at most 2 MiB); None when it cannot be read."""
    try:
        async with asyncio.timeout(TILE_SECONDS):
            return await bot.client.download(f"/attachments/{attachment_id}", max_bytes=TILE_BYTES)
    except (ApiError, TimeoutError):
        return None


async def guarded(ctx, cooldown, run):
    """Resolves the invoker's call, applies the cooldown and the one-at-a-time rule, then runs `run(channel_id, name)`."""
    found = await where.resolve(ctx)
    if isinstance(found, str):
        await ctx.reply_ephemeral(found, public_fallback=True)
        return
    channel_id, name = found
    if channel_id in busy:
        await ctx.reply_ephemeral("I am still drawing in your call; try again in a moment.", public_fallback=True)
        return
    wait = cooldown - (time.monotonic() - last_draw.get(ctx.author.id, -cooldown))
    if wait > 0:
        await ctx.reply_ephemeral(f"you can draw again in {int(wait) + 1}s.", public_fallback=True)
        return
    last_draw[ctx.author.id] = time.monotonic()
    busy.add(channel_id)
    try:
        async with ctx.typing():
            await run(channel_id, name)
    finally:
        busy.discard(channel_id)


@bot.group(name="draw", help="`<prompt>` draws it in your call, `trace <word>` traces a picture, `undo` removes your latest, `clear` removes them all")
async def draw(ctx, query: str = ""):
    query = query.strip()
    if not query:
        await ctx.reply_ephemeral(f"`{bot.prefix}draw <what to draw>`, `{bot.prefix}draw trace <word>`, `{bot.prefix}draw undo`, `{bot.prefix}draw clear`", public_fallback=True)
        return
    if planner is None:
        await _trace_request(ctx, query)
        return
    if len(query) > MAX_PROMPT:
        await ctx.reply_ephemeral(f"keep it under {MAX_PROMPT} characters.", public_fallback=True)
        return
    await guarded(ctx, AI_COOLDOWN, lambda channel_id, name: _ai_draw(ctx, query, channel_id, name))


async def _ai_draw(ctx, prompt, channel_id, name):
    canvas = bot.canvas(channel_id)
    window = plan.Region(VIEW_X - 800, VIEW_Y - 500, 1600, 1000)
    try:
        objects = await screenshot.read_objects(canvas, window)
        free = screenshot.largest_free(objects, window)
        if free is None:
            await ctx.reply_ephemeral(f"there is no free space on {name}'s canvas near the middle; clear some room and ask again.", public_fallback=True)
            return
        png = await screenshot.render(objects, window, free, fetch_tile)
    except screenshot.ScreenshotError as err:
        await ctx.reply_ephemeral(f"{err}.", public_fallback=True)
        return
    if not await bot.store.run(take_ai_call, time.strftime("%Y-%m-%d", time.gmtime()), AI_DAILY_CAP):
        await ctx.reply_ephemeral("I have used up today's drawing budget; try again tomorrow.", public_fallback=True)
        return
    try:
        calls = await planner.plan(defuse(prompt), png, free)
    except ai.Refused as err:
        await ctx.reply_ephemeral(f"{err}; try describing something else.", public_fallback=True)
        return
    except ai.PlannerError as err:
        await ctx.reply_ephemeral(f"I could not get a drawing: {err}.", public_fallback=True)
        return
    drawn, problems = plan.validate_plan(calls, free, MAX_STROKES)
    if not drawn:
        await ctx.reply_ephemeral("the model's plan had nothing I could place" + (f" ({problems[0]})" if problems else "") + "; try again.", public_fallback=True)
        return
    drawing_id, _ = await bot.store.run(new_drawing, ctx.author.id, prompt, channel_id, False)
    try:
        done = await place_all(canvas, drawing_id, drawn)
    except ApiError as err:
        await ctx.reply_ephemeral(f"the canvas stopped me after part of it (http {err.status}); `{bot.prefix}draw undo` removes what is there.", public_fallback=True)
        return
    skipped = f" ({len(problems)} parts of the plan were skipped)" if problems else ""
    await ctx.reply(f"drew \"{defuse(prompt)}\" on the canvas in {name} with {done} objects{skipped}.")


async def _trace_request(ctx, query):
    if len(query) > MAX_QUERY:
        await ctx.reply_ephemeral(f"keep it under {MAX_QUERY} characters.", public_fallback=True)
        return
    await guarded(ctx, COOLDOWN_SECONDS, lambda channel_id, name: _trace(ctx, query, channel_id))


async def _trace(ctx, query, channel_id):
    try:
        outcome = await find_drawable(query)
    except sources.SourceError as err:
        await ctx.reply_ephemeral(f"{err}.", public_fallback=True)
        return
    if isinstance(outcome, str):
        await ctx.reply_ephemeral(outcome, public_fallback=True)
        return
    candidate, pieces, width, height = outcome
    started = await bot.store.run(new_drawing, ctx.author.id, query, channel_id, True)
    if started is None:
        await ctx.reply_ephemeral(f"the drawing area holds {SLOTS} drawings; `{bot.prefix}draw undo` or `clear` makes room.", public_fallback=True)
        return
    drawing_id, slot = started
    try:
        done = await place_all(bot.canvas(channel_id), drawing_id, pieces_to_objects(slot, pieces, width, height))
    except ApiError as err:
        await ctx.reply_ephemeral(f"the canvas stopped me after part of it (http {err.status}); `{bot.prefix}draw undo` removes what is there.", public_fallback=True)
        return
    await ctx.reply(credit(candidate, defuse(query), done))


@draw.command(name="trace", help="Trace an openly licensed picture of a word")
async def draw_trace(ctx, query: str = ""):
    query = query.strip()
    if not query:
        await ctx.reply_ephemeral(f"`{bot.prefix}draw trace <word>`", public_fallback=True)
        return
    await _trace_request(ctx, query)


@draw.command(name="undo", help="Remove your latest drawing, all of it at once")
async def draw_undo(ctx):
    row = await bot.store.run(latest_live, ctx.author.id)
    if row is None:
        await ctx.reply_ephemeral("you have no drawing to undo.", public_fallback=True)
        return
    await remove_strokes(bot.canvas(row[2]), await bot.store.run(stroke_ids, row[0]))
    await bot.store.run(retire, row[0])
    await ctx.reply_ephemeral(f"removed your drawing of \"{defuse(row[1])}\".", public_fallback=True)


@draw.command(name="clear", help="Remove every drawing this bot made in your call", requires=Permissions.MANAGE_CANVAS, requires_message="only someone with Manage Canvas can clear the drawings.")
async def draw_clear(ctx):
    found = await where.find_call(ctx)
    if isinstance(found, str):
        await ctx.reply_ephemeral(found, public_fallback=True)
        return
    canvas = bot.canvas(found[0])
    drawings = await bot.store.run(live_drawings, found[0])
    for drawing_id in drawings:
        await remove_strokes(canvas, await bot.store.run(stroke_ids, drawing_id))
        await bot.store.run(retire, drawing_id)
    await ctx.reply_ephemeral(f"removed {len(drawings)} drawing{'s' if len(drawings) != 1 else ''}.", public_fallback=True)


if __name__ == "__main__":
    bot.run_cli()
