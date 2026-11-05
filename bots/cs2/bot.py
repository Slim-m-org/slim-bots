#!/usr/bin/env python3
"""bot-cs2: opt-in Steam presence ("X just started CS2"), `!cs2 lfg` and the group's server status; see README.md."""

import asyncio

import steam
from slimbots import ApiError, Bot

MAX_LINKED = 50
MAX_NOTE = 100


def init_db(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS players (user_id TEXT PRIMARY KEY, steam_id TEXT NOT NULL, quiet INTEGER NOT NULL DEFAULT 0, in_cs2 INTEGER NOT NULL DEFAULT 0)"
    )
    conn.commit()


bot = Bot(prefix="!", default_data_path="cs2.db", store_migrate=init_db)

STEAM_API_KEY = bot.setting("STEAM_API_KEY")
ANNOUNCE_CHANNEL = bot.setting("CS2_CHANNEL")
LFG_ROLE = bot.setting("CS2_ROLE")
LFG_MIN = max(bot.setting("CS2_LFG_MIN", 3, type=int), 1)
SERVER = bot.setting("CS2_SERVER")
POLL_SECONDS = max(bot.setting("CS2_POLL", 90, type=int), 60)

get = steam.http_get
query_server = steam.query_server
NO_KEY = "this needs a Steam Web API key, and the operator has not set STEAM_API_KEY."


def link(conn, user_id, steam_id):
    conn.execute(
        "INSERT INTO players (user_id, steam_id) VALUES (?, ?) ON CONFLICT (user_id) DO UPDATE SET steam_id = excluded.steam_id, in_cs2 = 0",
        (user_id, steam_id),
    )
    conn.commit()


def forget(conn, user_id):
    cur = conn.execute("DELETE FROM players WHERE user_id = ?", (user_id,))
    conn.commit()
    return cur.rowcount > 0


def toggle_quiet(conn, user_id):
    conn.execute("UPDATE players SET quiet = 1 - quiet WHERE user_id = ?", (user_id,))
    conn.commit()
    row = conn.execute("SELECT quiet FROM players WHERE user_id = ?", (user_id,)).fetchone()
    return None if row is None else bool(row[0])


def all_players(conn):
    return conn.execute("SELECT user_id, steam_id, quiet, in_cs2 FROM players").fetchall()


def set_in_cs2(conn, user_id, value):
    conn.execute("UPDATE players SET in_cs2 = ? WHERE user_id = ?", (value, user_id))
    conn.commit()


def count_players(conn):
    return conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]


def steam_id_from(text):
    """The text of a profile link or name reduced to what Steam's lookup wants: an id or a vanity name."""
    text = text.strip().strip("/")
    for marker in ("/profiles/", "/id/"):
        if marker in text:
            return text.split(marker, 1)[1].split("/")[0]
    return text.rsplit("/", 1)[-1]


async def summaries(players):
    return await asyncio.to_thread(steam.player_summaries, get, STEAM_API_KEY, [p[1] for p in players])


async def name_of(user_id):
    member = await bot.space.find_member(user_id)
    return member.mention() if member else "someone"


async def reply(ctx, text):
    await ctx.reply_ephemeral(text, public_fallback=True)


@bot.group(name="cs2", help="`link`, `forget`, `quiet`, `who`, `lfg`, `server`")
async def cs2(ctx, rest: str = ""):
    p = bot.prefix
    await reply(ctx, f"`{p}cs2 link <Steam id or profile link>`, `{p}cs2 forget`, `{p}cs2 quiet`, `{p}cs2 who`, `{p}cs2 lfg [note]`, `{p}cs2 server`")


@cs2.command(name="link", help="Opt in: let the bot read your Steam status and announce when you start CS2", usage="<17-digit Steam id, profile link or profile name>")
async def cs2_link(ctx, who: str = ""):
    if not STEAM_API_KEY:
        await reply(ctx, NO_KEY)
        return
    wanted = steam_id_from(who)
    if not wanted:
        await reply(ctx, f"give your Steam id or profile link, for example `{bot.prefix}cs2 link 76561198000000000`. Your Steam game details must be public.")
        return
    if await bot.store.run(count_players) >= MAX_LINKED:
        await reply(ctx, f"{MAX_LINKED} members are linked already.")
        return
    try:
        steam_id = wanted if steam.is_steam_id(wanted) else await asyncio.to_thread(steam.resolve_vanity, get, STEAM_API_KEY, wanted[:64])
        found = await asyncio.to_thread(steam.player_summaries, get, STEAM_API_KEY, [steam_id]) if steam_id else {}
    except steam.SteamError as err:
        await reply(ctx, f"I could not check that with Steam: {err}.")
        return
    if steam_id not in found:
        await reply(ctx, "Steam has no profile for that; check the id or link.")
        return
    await bot.store.run(link, ctx.author.id, steam_id)
    tail = f" It will say so in the announcement channel when you start CS2; `{bot.prefix}cs2 quiet` stops that." if ANNOUNCE_CHANNEL else ""
    await reply(ctx, f"linked to {found[steam_id]['name'] or 'that profile'}. `{bot.prefix}cs2 forget` removes the link any time.{tail}")


@cs2.command(name="forget", help="Remove your Steam link and everything the bot keeps about you")
async def cs2_forget(ctx):
    removed = await bot.store.run(forget, ctx.author.id)
    await reply(ctx, "your Steam link is removed." if removed else "you are not linked.")


@cs2.command(name="quiet", help="Stop or resume the announcement when you start CS2; you stay linked for lfg")
async def cs2_quiet(ctx):
    quiet = await bot.store.run(toggle_quiet, ctx.author.id)
    if quiet is None:
        await reply(ctx, f"you are not linked; use `{bot.prefix}cs2 link` first.")
        return
    await reply(ctx, "announcements are off for you." if quiet else "announcements are on for you.")


def describe(summary):
    if summary is None:
        return "profile not found"
    if summary["game_id"] == steam.CS2_APP_ID:
        return "in CS2"
    if summary["game_id"]:
        return "in a game"
    return "online" if summary["state"] > 0 else "offline"


@cs2.command(name="who", help="Who has linked and what they are doing on Steam")
async def cs2_who(ctx):
    if not STEAM_API_KEY:
        await reply(ctx, NO_KEY)
        return
    players = await bot.store.run(all_players)
    if not players:
        await reply(ctx, f"nobody has linked yet; `{bot.prefix}cs2 link` opts you in.")
        return
    try:
        found = await summaries(players)
    except steam.SteamError as err:
        await reply(ctx, f"Steam did not answer: {err}.")
        return
    await reply(ctx, "\n".join([f"- {await name_of(user_id)}: {describe(found.get(steam_id))}" for user_id, steam_id, _q, _c in players]))


@cs2.command(name="lfg", help="Ask for a game: pings the CS2 role when enough linked members are online and free", usage="[note]", cooldown=600, cooldown_bucket="deployment")
async def cs2_lfg(ctx, note: str = ""):
    if not STEAM_API_KEY:
        await reply(ctx, NO_KEY)
        return
    players = await bot.store.run(all_players)
    try:
        found = await summaries(players) if players else {}
    except steam.SteamError as err:
        await reply(ctx, f"Steam did not answer: {err}.")
        return
    free = [p[0] for p in players if describe(found.get(p[1])) == "online"]
    if len(free) < LFG_MIN:
        await reply(ctx, f"only {len(free)} linked member{'s are' if len(free) != 1 else ' is'} online and free right now; I ping at {LFG_MIN}.")
        return
    mentions = " ".join([await name_of(u) for u in free])
    lead = f"@{LFG_ROLE}" if LFG_ROLE else mentions
    extra = f" - {note.strip().replace('@', ' at ')[:MAX_NOTE]}" if note.strip() else ""
    await ctx.reply(f"{lead} {ctx.author.mention()} wants a CS2 game{extra}. Free now: {mentions}")


@cs2.command(name="server", help="Players and map on the group's CS2 server")
async def cs2_server(ctx):
    if not SERVER or ":" not in SERVER:
        await reply(ctx, "no server is set up; the operator sets CS2_SERVER to host:port.")
        return
    host, _, port = SERVER.rpartition(":")
    try:
        info = await asyncio.to_thread(query_server, host, int(port))
    except (steam.SteamError, ValueError) as err:
        await reply(ctx, f"the server did not answer: {err}.")
        return
    await ctx.reply(f"{info['name']}: {info['players']}/{info['max_players']} players on {info['map']}.")


async def announce(players, found):
    for user_id, steam_id, quiet, was_in in players:
        playing = (found.get(steam_id) or {}).get("game_id") == steam.CS2_APP_ID
        if playing == bool(was_in):
            continue
        await bot.store.run(set_in_cs2, user_id, int(playing))
        if playing and not quiet:
            await bot.client.send(ANNOUNCE_CHANNEL, f"{await name_of(user_id)} just started CS2.")


@bot.every(POLL_SECONDS, name="cs2", on="ready", enabled=lambda: bool(STEAM_API_KEY and ANNOUNCE_CHANNEL))
async def poll():
    players = await bot.store.run(all_players)
    if not players:
        return
    try:
        await announce(players, await summaries(players))
    except steam.SteamError as err:
        print(f"cs2: Steam poll failed: {err}")
    except ApiError as err:
        print(f"cs2: the announcement channel refused a post (http {err.status})")


if __name__ == "__main__":
    bot.run_cli()
