#!/usr/bin/env python3
"""bot-homelab: keeps Proxmox, Docker and Jellyfin status tiles up to date on a voice channel's canvas; see README.md."""

import asyncio
import functools
import time
import uuid

import sources
from slimbots import ApiError, Bot, Permissions

TILE_W = 260.0
TILE_H = 150.0
GAP = 20.0


def init_db(conn):
    conn.execute("CREATE TABLE IF NOT EXISTS tiles (name TEXT PRIMARY KEY, object_id TEXT NOT NULL, text TEXT NOT NULL)")
    conn.commit()


bot = Bot(prefix="!", default_data_path="homelab.db", store_migrate=init_db)

CANVAS_CHANNEL = bot.setting("HOMELAB_CHANNEL")
INTERVAL = max(bot.setting("HOMELAB_INTERVAL", 30, type=int), 10)
ORIGIN_X = bot.setting("HOMELAB_X", 400.0, type=float)
ORIGIN_Y = bot.setting("HOMELAB_Y", 0.0, type=float)
PROXMOX_URL = bot.setting("HOMELAB_PROXMOX_URL")
PROXMOX_TOKEN = bot.setting("HOMELAB_PROXMOX_TOKEN")
PROXMOX_VERIFY_TLS = bot.setting("HOMELAB_PROXMOX_VERIFY_TLS", True, type=bool)
DOCKER_SOCKET = bot.setting("HOMELAB_DOCKER_SOCKET")
JELLYFIN_URL = bot.setting("HOMELAB_JELLYFIN_URL")
JELLYFIN_KEY = bot.setting("HOMELAB_JELLYFIN_KEY")

get_url = sources.http_get
get_docker = sources.docker_get
canvas = None
canvas_channel_name = None
# tile name -> "ok" or the sentence for the last problem, for `!homelab`.
status = {}
last_poll = [0.0]


def tile_names():
    """The tiles this configuration can draw, in canvas order."""
    names = []
    if PROXMOX_URL and PROXMOX_TOKEN:
        names.append("proxmox")
    if DOCKER_SOCKET:
        names.append("docker")
    if JELLYFIN_URL and JELLYFIN_KEY:
        names.append("jellyfin")
    return names


def reader_for(name):
    """(title, a sync reader returning the tile lines) for one tile."""
    if name == "proxmox":
        get = functools.partial(get_url, verify=PROXMOX_VERIFY_TLS)
        return "Proxmox", lambda: sources.proxmox_tile(sources.read_proxmox(get, PROXMOX_URL, PROXMOX_TOKEN))
    if name == "docker":
        return "Containers", lambda: sources.docker_tile(sources.read_docker(get_docker, DOCKER_SOCKET))
    return "Jellyfin", lambda: sources.jellyfin_tile(sources.read_jellyfin(get_url, JELLYFIN_URL, JELLYFIN_KEY))


async def read_tile(name):
    title, read = reader_for(name)
    try:
        return sources.tile_text(await asyncio.to_thread(read)), "ok"
    except sources.SourceError as err:
        return sources.tile_text(sources.unreachable_tile(title, err)), f"{title} is unreachable: {err}"


def get_tile(conn, name):
    return conn.execute("SELECT object_id, text FROM tiles WHERE name = ?", (name,)).fetchone()


def save_tile(conn, name, object_id, text):
    conn.execute(
        "INSERT INTO tiles (name, object_id, text) VALUES (?, ?, ?) "
        "ON CONFLICT (name) DO UPDATE SET object_id = excluded.object_id, text = excluded.text",
        (name, object_id, text),
    )
    conn.commit()


def forget_tiles(conn, object_ids=None):
    if object_ids is None:
        conn.execute("DELETE FROM tiles")
    else:
        conn.executemany("DELETE FROM tiles WHERE object_id = ?", [(i,) for i in object_ids])
    conn.commit()


def all_tiles(conn):
    return [r[0] for r in conn.execute("SELECT object_id FROM tiles")]


async def edit_text(object_id, text):
    """Replaces a note's text in place; the canvas has no Canvas.edit yet, so this posts the op itself."""
    body = {"id": str(uuid.uuid4()), "kind": "edit", "object_id": object_id, "props": {"text": text}}
    return await bot.client.call("POST", f"/channels/{canvas.channel_id}/canvas/ops", body)


async def draw(name, text):
    """Updates the tile in place, or places it when it was never drawn or someone removed it."""
    row = await bot.store.run(get_tile, name)
    if row and row[1] == text:
        return
    if row:
        try:
            done = await edit_text(row[0], text)
        except ApiError as err:
            if err.status != 404:
                raise
            done = {"affected": 0}
        if (done or {}).get("affected", 1) != 0:
            await bot.store.run(save_tile, name, row[0], text)
            return
    x = ORIGIN_X + tile_names().index(name) * (TILE_W + GAP)
    placed = await canvas.place("note", x=x, y=ORIGIN_Y, w=TILE_W, h=TILE_H, props={"text": text})
    await bot.store.run(save_tile, name, placed["id"], text)


async def refresh():
    if canvas is None:
        return
    for name in tile_names():
        text, state = await read_tile(name)
        status[name] = state
        try:
            await draw(name, text)
        except ApiError as err:
            status[name] = f"the canvas refused the update (http {err.status}); check that I can use the canvas"
    last_poll[0] = time.time()


@bot.every(INTERVAL, name="homelab", on="ready", delay_first=False)
async def poll():
    await refresh()


@bot.event
async def on_canvas_objects_removed(frame):
    await bot.store.run(forget_tiles, list(frame.get("object_ids") or []))


@bot.event
async def on_canvas_cleared(frame):
    await bot.store.run(forget_tiles)


def resolve_channel():
    wanted = CANVAS_CHANNEL or bot.channel
    channel = bot.space.get_channel(wanted) if wanted else None
    if channel is None:
        raise RuntimeError("set HOMELAB_CHANNEL to the voice channel whose canvas shows the tiles")
    if channel.kind != "voice":
        raise RuntimeError(f"{wanted!r} is a {channel.kind} channel - the canvas belongs to a voice channel")
    return channel


async def _refuse_to_start(err):
    """A handler's own exception is swallowed; one out of a background task stops the bot."""
    raise err


@bot.event
async def on_connect():
    global canvas, canvas_channel_name
    try:
        channel = resolve_channel()
    except RuntimeError as err:
        bot.background(_refuse_to_start(err), name="homelab-config")
        return
    canvas_channel_name = channel.name
    canvas = bot.canvas(channel.id)


def missing_sources_text():
    p = bot.prefix
    return (
        f"no source is set up, so there is nothing to draw. Set HOMELAB_PROXMOX_URL and HOMELAB_PROXMOX_TOKEN, "
        f"HOMELAB_DOCKER_SOCKET, or HOMELAB_JELLYFIN_URL and HOMELAB_JELLYFIN_KEY, then restart. `{p}homelab` shows this again."
    )


def status_text():
    names = tile_names()
    if not names:
        return missing_sources_text()
    where = f"drawing on #{canvas_channel_name}" if canvas_channel_name else "the canvas channel is not resolved yet"
    lines = [f"{where}, every {INTERVAL}s"]
    lines += [f"- {name}: {status.get(name, 'not polled yet')}" for name in names]
    return "\n".join(lines)


@bot.command(name="homelab", help="Show what is drawn and any problem, or `redraw` the tiles", usage="[redraw]", choices={"sub": ("status", "redraw")})
async def homelab(ctx, sub: str = "status"):
    if sub.lower() == "redraw":
        if not ctx.author.has_permission(Permissions.MANAGE_SERVER):
            await ctx.reply_ephemeral("only someone with Manage Server can redraw the tiles.", public_fallback=True)
            return
        ids = await bot.store.run(all_tiles)
        if ids and canvas is not None:
            await canvas.remove(ids)
        await bot.store.run(forget_tiles)
        await refresh()
    await ctx.reply_ephemeral(status_text(), public_fallback=True)


if __name__ == "__main__":
    bot.run_cli()
