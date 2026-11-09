#!/usr/bin/env python3
"""bot-draw: `!draw <prompt>` has a model draw on the canvas of the call you are in; `!draw trace <query>` traces a picture; see README.md."""

import asyncio
import contextlib
import functools
import inspect
import itertools
from dataclasses import replace
import time
import uuid

import ai
import colorize
import crop
import paste
import photo
import plan
import runlog
import screenshot
import sources
import squiggle
import store_db
import tracer
import where
from slimbots import ApiError, Bot, Permissions

try:
    from slimbots import BatchUnavailable
except ImportError:  # slimbots before 0.18 has no batch place

    class BatchUnavailable(Exception):
        pass
from slimbots.http import is_rate_limited
from store_db import SLOTS, add_stroke, add_strokes, latest_live, live_drawings, new_drawing, retire, stroke_ids, take_ai_call

REMOVE_BATCH = 64  # the server's most object ids in one remove op
REMOVE_PARALLEL = 3
BATCH_SIZE = 200  # the server's most objects in one batch placement
PER_ROW = 3
STROKE_WIDTH = 3.0
MAX_QUERY = 60
MAX_PROMPT = 200
TILE_BYTES = 2 * 1024 * 1024
TILE_SECONDS = 10
ATTEMPTS = 3
PICTURE_TRIES = 8
RATE_RETRIES = 4
RATE_BACKOFF = 1.5


bot = Bot(prefix="!", default_data_path="draw.db", store_migrate=store_db.init_db, listen_voice_chats=True)

ORIGIN_X = bot.setting("DRAW_X", 900.0, type=float)
ORIGIN_Y = bot.setting("DRAW_Y", 600.0, type=float)
SIZE = bot.setting("DRAW_SIZE", 480.0, type=float)
MAX_STROKES = min(max(bot.setting("DRAW_MAX_STROKES", 150, type=int), 10), 300)
PACE_SECONDS = max(bot.setting("DRAW_PACE", 0.15, type=float), 0.1)
GOOGLE_KEY = bot.setting("GOOGLE_CSE_KEY")
GOOGLE_ENGINE = bot.setting("GOOGLE_CSE_ID")
GOOGLE = (GOOGLE_KEY, GOOGLE_ENGINE) if GOOGLE_KEY and GOOGLE_ENGINE else None
SERPAPI_KEY = bot.setting("SERPAPI_KEY")
VIEW_X = bot.setting("DRAW_VIEW_X", 0.0, type=float)
VIEW_Y = bot.setting("DRAW_VIEW_Y", 0.0, type=float)
SHADED_MAX = min(max(bot.setting("DRAW_SHADED_MAX", bot.setting("DRAW_DOTS_MAX", 1500, type=int), type=int), 200), 4000)
PHOTO_MAX = min(max(bot.setting("DRAW_PHOTO_MAX", 300, type=int), 20), 1500)
COLOR_MAX = min(max(bot.setting("DRAW_COLOR_MAX", 2000, type=int), 200), 4000)
PHOTO_SHADING = bot.setting("DRAW_PHOTO_SHADING", False, type=bool)
BOARD = "light" if str(bot.setting("DRAW_BOARD", "dark")).lower() == "light" else "dark"
AI_TIME_CAP = max(bot.setting("DRAW_AI_TIME_CAP", 180, type=int), 30)
AI_REQUEST_TIMEOUT = max(bot.setting("DRAW_AI_REQUEST_TIMEOUT", 90, type=int), 10)
AI_COOLDOWN = bot.setting("DRAW_AI_COOLDOWN", 120, type=int)
AI_DAILY_CAP = bot.setting("DRAW_AI_DAILY_CAP", 100, type=int)
AI_MAX_TOKENS = min(max(bot.setting("DRAW_AI_MAX_TOKENS", 16000, type=int), 1536), 32000)
AI_THINKING_BUDGET = bot.setting("DRAW_AI_THINKING_BUDGET", 2048, type=int)
GRID = 96

get = sources.http_get
get_picture = sources.http_get_public
planner = ai.make_planner(
    api_key=bot.setting("ANTHROPIC_API_KEY"), model=bot.setting("DRAW_MODEL", "claude-sonnet-4-6"), max_tokens=AI_MAX_TOKENS,
    local_url=bot.setting("DRAW_LLM_URL"), thinking_budget=AI_THINKING_BUDGET, request_timeout=AI_REQUEST_TIMEOUT, local_model=bot.setting("DRAW_LLM_MODEL"), local_key=bot.setting("DRAW_LLM_KEY") or "",
)
COOLDOWN_SECONDS = 60
# user id -> monotonic time of that member's last drawing, so one person cannot keep the pen busy.
last_draw = {}
# Whether the server has the batch placement route: None until first tried, False once it answered 404 or 405.
batch_state = {"available": None}
# Channels with a drawing in progress: one at a time per canvas.
busy = set()


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


def grouped(objects):
    """The objects with one shared `props.group`, which the client reads as a single undo step."""
    group = str(uuid.uuid4())
    return [replace(o, props={**o.props, "group": group}) for o in objects]


async def batch_with_retry(canvas, wire):
    if not hasattr(canvas, "place_many"):
        raise BatchUnavailable("this slimbots has no batch place")
    for attempt in range(RATE_RETRIES):
        try:
            return await canvas.place_many(wire)
        except ApiError as err:
            if not is_rate_limited(err) or attempt == RATE_RETRIES - 1:
                raise
            await asyncio.sleep(RATE_BACKOFF * (attempt + 1))


async def place_batches(canvas, drawing_id, objects):
    """Places `objects` in batches; returns how many went down, or None when the server has no batch route (noted for the process)."""
    done = 0
    for start in range(0, len(objects), BATCH_SIZE):
        chunk = objects[start : start + BATCH_SIZE]
        wire = [{"kind": o.kind, "x": o.x, "y": o.y, "w": o.w, "h": o.h, "props": o.props} for o in chunk]
        try:
            ids = await batch_with_retry(canvas, wire)
        except BatchUnavailable:
            batch_state["available"] = False
            return done if done else None
        await bot.store.run(add_strokes, drawing_id, ids)
        done += len(ids)
    return done


async def place_all(canvas, drawing_id, objects):
    """Places the drawing as one undo group and keeps the ids for undo; batches when the server can, else paced singles; returns the count."""
    objects = grouped(objects)
    done = 0
    if batch_state["available"] is not False:
        placed = await place_batches(canvas, drawing_id, objects)
        if placed is not None and batch_state["available"] is not False:
            return placed
        done = placed or 0
    for planned in objects[done:]:
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
    found = await asyncio.to_thread(lambda: list(itertools.islice(sources.find_pictures(get, query, GOOGLE), ATTEMPTS)))
    if not found:
        return "no openly licensed picture matched that; try a simpler word."
    for candidate in found[:ATTEMPTS]:
        try:
            data = await asyncio.to_thread(sources.fetch_picture, get_picture, candidate["url"])
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
    return f"drew \"{query}\" with {count} strokes from {defuse(sources.credit_line(candidate))}"


async def remove_batch(canvas, ids, gate):
    async with gate:
        for attempt in range(RATE_RETRIES):
            try:
                return await canvas.remove(ids)
            except ApiError as err:
                if not is_rate_limited(err) or attempt == RATE_RETRIES - 1:
                    raise
                await asyncio.sleep(RATE_BACKOFF * (attempt + 1))


async def remove_strokes(canvas, object_ids):
    """Removes the ids in ops of REMOVE_BATCH, a few at a time, backing off when the server says slow down."""
    gate = asyncio.Semaphore(REMOVE_PARALLEL)
    await asyncio.gather(*(remove_batch(canvas, object_ids[start : start + REMOVE_BATCH], gate) for start in range(0, len(object_ids), REMOVE_BATCH)))


def defuse(text):
    return text.replace("@", " at ").replace("<", " ").replace(">", " ")


async def fetch_tile(attachment_id):
    """An image tile's bytes for the screenshot (at most 2 MiB); None when it cannot be read."""
    try:
        async with asyncio.timeout(TILE_SECONDS):
            return await bot.client.download(f"/attachments/{attachment_id}", max_bytes=TILE_BYTES)
    except (ApiError, TimeoutError):
        return None


async def refuse(ctx, log, outcome, text):
    """A pure error: logged with its outcome and shown only to the invoker."""
    log.done(outcome)
    await ctx.reply_ephemeral(text, public_fallback=True)


async def guarded(ctx, cooldown, run, *, command, prompt, ack):
    """Resolves the invoker's call, applies the cooldown and one-at-a-time rule, acks publicly, runs `run(channel_id, name, log)`; every path logs one line."""
    log = runlog.RunLog(command, ctx.channel_id, prompt)
    owned = None
    try:
        found = await where.resolve(ctx)
        if isinstance(found, str):
            await refuse(ctx, log, "no call" if "join a voice channel" in found else "refused: missing rights", found)
            return
        channel_id, name = found
        log.channel_id = channel_id
        if channel_id in busy:
            await refuse(ctx, log, "busy", "I am still drawing in your call; try again in a moment.")
            return
        wait = cooldown - (time.monotonic() - last_draw.get(ctx.author.id, -cooldown))
        if wait > 0:
            await refuse(ctx, log, "cooldown", f"you can draw again in {int(wait) + 1}s.")
            return
        last_draw[ctx.author.id] = time.monotonic()
        busy.add(channel_id)
        owned = channel_id
        await ctx.reply(ack)
        async with ctx.typing():
            await run(channel_id, name, log)
        log.done("finished")
    except Exception as err:  # a handler's own exception is swallowed by the framework, so it is logged here
        log.failed(err)
        with contextlib.suppress(Exception):
            await ctx.reply_ephemeral(f"something went wrong drawing that ({type(err).__name__}); it is in the bot's log.", public_fallback=True)
    finally:
        if owned is not None:
            busy.discard(owned)


MODES = ("photo", "shaded", "color", "paste", "trace", "undo", "clear")
# slimbots before 0.18 has no `suggest`, so the composer then just lacks the pick list
_PICKER = {"suggest": {"mode": MODES}} if "suggest" in inspect.signature(bot.group).parameters else {}


@bot.group(name="draw", help="Draw in your call: <idea>, photo, shaded, color, paste <subject>, trace <word>, undo, clear", **_PICKER)
async def draw(ctx, mode: str = ""):
    """The composer labels the first word `mode`; anything that is not a mode is the idea to draw."""
    query = mode.strip()
    if not query:
        await ctx.reply_ephemeral(
            f"`{bot.prefix}draw <idea>` an AI drawing, `{bot.prefix}draw photo <subject>` a line sketch of a photo, "
            f"`{bot.prefix}draw shaded <subject>` a tonal portrait, `{bot.prefix}draw color <subject>` the whole picture in colour, "
            f"`{bot.prefix}draw paste <subject>` the picture itself, `{bot.prefix}draw trace <word>` the old tracer, "
            f"`{bot.prefix}draw undo`, `{bot.prefix}draw clear`.",
            public_fallback=True,
        )
        return
    if planner is None:
        await _trace_request(ctx, query)
        return
    if len(query) > MAX_PROMPT:
        await ctx.reply_ephemeral(f"keep it under {MAX_PROMPT} characters.", public_fallback=True)
        return
    await guarded(
        ctx, AI_COOLDOWN, lambda channel_id, name, log: _ai_draw(ctx, query, channel_id, name, log),
        command="draw", prompt=query, ack=f"drawing {defuse(query)}, this takes a minute or two.",
    )


def make_review(free):
    """A `review(calls)` for the planner: a verdict per call, and the free region redrawn with everything accepted so far."""
    accepted = []

    async def review(calls):
        verdicts = []
        for tool, args in calls:
            try:
                made = plan.validate_call(tool, args, free)
            except plan.PlanError as err:
                verdicts.append((False, f"not drawn: {err}"))
                continue
            if sum(not o.guide for o in accepted) + sum(not o.guide for o in made) > MAX_STROKES:
                verdicts.append((False, f"not drawn: the {MAX_STROKES} object limit is reached; stop drawing"))
                continue
            accepted.extend(made)
            verdicts.append((True, f"drawn ({len(made)} object{'' if len(made) == 1 else 's'})"))
        wire = [{"kind": o.kind, "x": o.x, "y": o.y, "w": o.w, "h": o.h, "props": o.props, "guide": o.guide} for o in accepted]
        return verdicts, await screenshot.render(wire, screenshot.fit_view(free), None, ruler=100)

    return review


class Candidates:
    """The picture candidates for a subject, read lazily, counting the SerpAPI searches they cost."""

    def __init__(self, subject, log):
        self.serpapi_calls = 0
        self._found = iter(sources.find_pictures(get, subject, GOOGLE, log.note, SERPAPI_KEY, self._spent))

    def _spent(self):
        self.serpapi_calls += 1

    def __iter__(self):
        return self

    def __next__(self):
        return next(self._found)


def candidate_pictures(subject, log):
    """The picture candidates for `subject`, best source first, read lazily (each read may call a source)."""
    return Candidates(subject, log)


async def note_serpapi_spend(candidates, log):
    """Adds this lookup's SerpAPI searches to the month's tally and puts the total in the log line."""
    calls = getattr(candidates, "serpapi_calls", 0)
    if calls:
        total = await bot.store.run(store_db.add_serpapi_calls, time.strftime("%Y-%m", time.gmtime()), calls)
        log.set("serpapi_month", total)


async def first_picture(candidates, log, convert=None):
    """(bytes, candidate) for the first of up to PICTURE_TRIES candidates that downloads (and converts), trying a result's thumbnail when its original fails; None when none do."""
    try:
        return await _first_picture(candidates, log, convert)
    finally:
        await note_serpapi_spend(candidates, log)


async def _first_picture(candidates, log, convert):
    for _ in range(PICTURE_TRIES):
        with log.stage("search"):
            candidate = await asyncio.to_thread(next, candidates, None)
        if candidate is None:
            return None
        for url in [candidate["url"], *([candidate["fallback"]] if candidate.get("fallback") else [])]:
            try:
                with log.stage("download"):
                    data = await asyncio.to_thread(sources.fetch_picture, get_picture, url)
                    if convert:
                        data = await asyncio.to_thread(convert, data)
            except sources.SourceError as err:
                log.note(f"{candidate.get('source', '?')}: {err}")
                continue
            log.source = candidate.get("source", "unknown")
            return data, candidate
    return None


async def find_reference(prompt, log):
    """(reference for the model, candidate) of the best freely licensed picture of the subject, or None; never raises."""
    try:
        return await first_picture(candidate_pictures(prompt[:100], log), log, sources.reference_image)
    except sources.SourceError:
        return None


async def _ai_draw(ctx, prompt, channel_id, name, log):
    deadline = time.monotonic() + AI_TIME_CAP
    canvas = bot.canvas(channel_id)
    window = plan.Region(VIEW_X - 800, VIEW_Y - 500, 1600, 1000)
    try:
        with log.stage("canvas"):
            objects = await screenshot.read_objects(canvas, window)
            free = screenshot.largest_free(objects, window)
            if free is None:
                await refuse(ctx, log, "no free space", f"there is no free space on {name}'s canvas near the middle; clear some room and ask again.")
                return
            png = await screenshot.render(objects, screenshot.View(window), free, fetch_tile)
    except screenshot.ScreenshotError as err:
        await refuse(ctx, log, "refused: no pillow", f"{err}.")
        return
    if not await bot.store.run(take_ai_call, time.strftime("%Y-%m-%d", time.gmtime()), AI_DAILY_CAP):
        await refuse(ctx, log, "daily cap", "I have used up today's drawing budget; try again tomorrow.")
        return
    found = await find_reference(prompt, log)
    reference, source = found if found else (None, None)
    try:
        with log.stage("model"):
            calls = await planner.plan(defuse(prompt), png, free, max_calls=MAX_STROKES, review=make_review(free), reference=reference, deadline=deadline)
    except ai.Refused as err:
        await refuse(ctx, log, f"refused: {err}", f"{err}; try describing something else.")
        return
    except ai.PlannerError as err:
        await refuse(ctx, log, f"model error: {err}", f"I could not get a drawing: {err}.")
        return
    drawn, problems = plan.validate_plan(calls, free, MAX_STROKES)
    if not drawn:
        await refuse(ctx, log, "nothing placeable", "the model's plan had nothing I could place" + (f" ({problems[0]})" if problems else "") + "; try again.")
        return
    drawing_id, _ = await bot.store.run(new_drawing, ctx.author.id, prompt, channel_id, False)
    try:
        with log.stage("place"):
            done = await place_all(canvas, drawing_id, drawn)
    except ApiError as err:
        await refuse(ctx, log, f"canvas error: http {err.status}", f"the canvas stopped me after part of it (http {err.status}); `{bot.prefix}draw undo` removes what is there.")
        return
    skipped = f" ({len(problems)} parts of the plan were skipped)" if problems else ""
    credited = f" Reference: {defuse(sources.credit_line(source))}" if source else ""
    log.done(f"drew {done}")
    await ctx.reply(f"drew \"{defuse(prompt)}\" on the canvas in {name} with {done} object{'' if done == 1 else 's'}{skipped}.{credited}")


async def _trace_request(ctx, query):
    if len(query) > MAX_QUERY:
        await ctx.reply_ephemeral(f"keep it under {MAX_QUERY} characters.", public_fallback=True)
        return
    await guarded(
        ctx, COOLDOWN_SECONDS, lambda channel_id, name, log: _trace(ctx, query, channel_id, log),
        command="trace", prompt=query, ack=f"tracing {defuse(query)}, one moment.",
    )


async def _trace(ctx, query, channel_id, log):
    try:
        with log.stage("search"):
            outcome = await find_drawable(query)
    except sources.SourceError as err:
        await refuse(ctx, log, f"no source: {err}", f"{err}.")
        return
    if isinstance(outcome, str):
        await refuse(ctx, log, "no source", outcome)
        return
    candidate, pieces, width, height = outcome
    log.source = "openverse"
    started = await bot.store.run(new_drawing, ctx.author.id, query, channel_id, True)
    if started is None:
        await refuse(ctx, log, "grid full", f"the drawing area holds {SLOTS} drawings; `{bot.prefix}draw undo` or `clear` makes room.")
        return
    drawing_id, slot = started
    try:
        with log.stage("place"):
            done = await place_all(bot.canvas(channel_id), drawing_id, pieces_to_objects(slot, pieces, width, height))
    except ApiError as err:
        await refuse(ctx, log, f"canvas error: http {err.status}", f"the canvas stopped me after part of it (http {err.status}); `{bot.prefix}draw undo` removes what is there.")
        return
    log.done(f"drew {done}")
    await ctx.reply(credit(candidate, defuse(query), done))


async def dots_source(ctx, subject, log):
    """(picture bytes, candidate or None) from an image attached to the command, else the best freely licensed picture of the subject; a sentence when neither works."""
    for attached in ctx.message.get("attachments") or []:
        if str(attached.get("content_type") or "").startswith("image/"):
            try:
                with log.stage("download"):
                    data = await bot.client.download(f"/attachments/{attached['id']}", max_bytes=sources.MAX_PICTURE_BYTES)
            except ApiError:
                return "I could not read the image you attached."
            log.source = "attachment"
            return data, None
    if not subject:
        return f"attach an image or say what to draw: `{bot.prefix}draw shaded <subject>`."
    try:
        found = await first_picture(candidate_pictures(subject, log), log)
    except sources.SourceError as err:
        return f"{err}."
    return found if found else "no picture matched that; try a simpler word."


async def crop_picture(data, log, detect_face):
    """(picture with flat borders cut, face box in it or None): the face is located for the tracer's edge thresholds, never zoomed on."""
    try:
        box, face_box, mode = await asyncio.to_thread(crop.choose, data, detect_face)
        log.set("crop", f"{mode} {crop.describe(box)}")
        return await asyncio.to_thread(crop.crop_bytes, data, box), face_box
    except sources.SourceError:
        return data, None


async def _picture_job(ctx, subject, channel_id, name, log, build, noun, focus="trim"):
    """Draws what `build(picture bytes, free region, face box)` makes from an attached image or the best freely licensed picture of the subject."""
    canvas = bot.canvas(channel_id)
    window = plan.Region(VIEW_X - 800, VIEW_Y - 500, 1600, 1000)
    try:
        with log.stage("canvas"):
            free = screenshot.largest_free(await screenshot.read_objects(canvas, window), window)
        if free is None:
            await refuse(ctx, log, "no free space", f"there is no free space on {name}'s canvas near the middle; clear some room and ask again.")
            return
        source = await dots_source(ctx, subject, log)
        if isinstance(source, str):
            await refuse(ctx, log, "no source", source)
            return
        data, candidate = source
        face_box = None
        if focus != "none":
            with log.stage("crop"):
                data, face_box = await crop_picture(data, log, focus == "face")
        with log.stage("trace"):
            if inspect.iscoroutinefunction(build):
                objects = await build(data, free, face_box)
            else:
                objects = await asyncio.to_thread(build, data, free, face_box)
    except (screenshot.ScreenshotError, sources.SourceError) as err:
        await refuse(ctx, log, f"refused: {err}", f"{err}.")
        return
    except ApiError as err:
        await refuse(ctx, log, f"upload failed: http {err.status}", f"I could not upload the picture (http {err.status}).")
        return
    if not objects:
        await refuse(ctx, log, "nothing to draw", "I could not find anything to draw in that picture.")
        return
    drawing_id, _ = await bot.store.run(new_drawing, ctx.author.id, subject or "an attached image", channel_id, False)
    try:
        with log.stage("place"):
            done = await place_all(canvas, drawing_id, objects)
    except ApiError as err:
        await refuse(ctx, log, f"canvas error: http {err.status}", f"the canvas stopped me after part of it (http {err.status}); `{bot.prefix}draw undo` removes what is there.")
        return
    credit_text = f" from {defuse(sources.credit_line(candidate))}" if candidate else ""
    log.done(f"drew {done}")
    await ctx.reply(f"drew {noun.format(count=done)} on the canvas in {name}{credit_text}")


async def _picture_command(ctx, subject, command, build, noun, ack, focus="trim"):
    subject = subject.strip()
    if len(subject) > MAX_QUERY:
        await ctx.reply_ephemeral(f"keep it under {MAX_QUERY} characters.", public_fallback=True)
        return
    await guarded(
        ctx, COOLDOWN_SECONDS, lambda channel_id, name, log: _picture_job(ctx, subject, channel_id, name, log, build, noun, focus),
        command=command, prompt=subject or "attachment", ack=ack.format(subject=defuse(subject) or "your image"),
    )


@draw.command(name="shaded", aliases=("dots",), help="Draw a tonal squiggle portrait of an attached image or a subject; no model, no cost")
async def draw_shaded(ctx, subject: str = ""):
    await _picture_command(
        ctx, subject, "shaded", lambda data, free, face: squiggle.squiggle(data, free, SHADED_MAX, BOARD), "a shaded portrait with {count} strokes",
        "making a shaded portrait of {subject}, this takes a minute or two.",
    )


@draw.command(name="color", help="Paint the whole picture in colour, as brushstrokes")
async def draw_color(ctx, subject: str = ""):
    await _picture_command(
        ctx, subject, "color", lambda data, free, face: colorize.color_strokes(data, free, COLOR_MAX), "the whole picture in colour with {count} strokes",
        "painting {subject} in colour, this takes a few minutes.",
    )


async def paste_picture(data, free, face):
    """One canvas image object: the picture uploaded as an attachment and sized into the free region."""
    jpeg, width, height = await asyncio.to_thread(paste.prepare, data)
    attachment = await bot.client.upload_attachment(jpeg, filename="draw.jpg")
    return [paste.image_object(attachment.id, width, height, free)]


@draw.command(name="paste", help="Put the picture itself on the canvas, no drawing")
async def draw_paste(ctx, subject: str = ""):
    await _picture_command(ctx, subject, "paste", paste_picture, "the picture as {count} image", "pasting {subject}, one moment.", focus="none")


@draw.command(name="photo", help="Trace a line drawing from a photo (attached or found for a subject); no model, no cost")
async def draw_photo(ctx, subject: str = ""):
    await _picture_command(
        ctx, subject, "photo", lambda data, free, face: photo.trace_photo(data, free, PHOTO_MAX, face, PHOTO_SHADING, BOARD), "a line drawing of the photo with {count} strokes",
        "tracing a line drawing of {subject}, this takes a minute or two.", focus="face",
    )


@draw.command(name="trace", help="Trace an openly licensed picture of a word")
async def draw_trace(ctx, query: str = ""):
    query = query.strip()
    if not query:
        await ctx.reply_ephemeral(f"`{bot.prefix}draw trace <word>`", public_fallback=True)
        return
    await _trace_request(ctx, query)


@draw.command(name="undo", help="Remove your latest drawing in your call, all of it at once")
async def draw_undo(ctx):
    found = await where.find_call(ctx)
    if isinstance(found, str):
        await ctx.reply_ephemeral(found, public_fallback=True)
        return
    row = await bot.store.run(latest_live, ctx.author.id, found[0])
    if row is None:
        await ctx.reply_ephemeral(f"you have no drawing to undo in {found[1]}.", public_fallback=True)
        return
    await remove_strokes(bot.canvas(found[0]), await bot.store.run(stroke_ids, row[0]))
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
