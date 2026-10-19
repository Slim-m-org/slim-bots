#!/usr/bin/env python3
"""bot-game-status: live up/down and player counts for game servers, in one status message per channel; see README.md.
Split per docs/framework.md: this is the entry point and the poll loop, gs_core.py and queries.py hold the rest."""

import asyncio
import datetime
import sys
import uuid

from slimbots import ApiError, Bot, Permissions, style
from slimbots.http import is_not_found, is_token_revoked

import gs_core as core
import queries

LIST_COOLDOWN_SECONDS = 10

bot = Bot(prefix="!", default_data_path="game-status.db", store_migrate=core.init_db)
core.configure(bot)


def now():
    return datetime.datetime.now(datetime.timezone.utc)


async def poll(servers):
    """Every server queried at once, each under its own timeout, so one dead host never delays the rest."""
    results = await asyncio.gather(*(queries.check(s["kind"], s["host"], s["port"], core.GAMESTATUS_TIMEOUT) for s in servers))
    return list(zip(servers, results))


async def create_board(channel_id, text):
    message_id = str(uuid.uuid4())
    await bot.client.send(channel_id, text, message_id=message_id)
    store = await bot.open_store()
    await store.run(core.txn_set_board, channel_id, message_id)


async def publish_board(channel_id, message_id, rows, stamp):
    """Edits the channel's plain-text status message (edits carry content only), or posts a fresh one if it was deleted."""
    text = core.status_text(rows, stamp)
    if message_id:
        try:
            await bot.client.edit_message(channel_id, message_id, text)
            return
        except ApiError as err:
            if not is_not_found(err):
                raise
    await create_board(channel_id, text)


async def publish_all(rows, stamp):
    """One channel failing (no access, deleted channel) is logged and never blocks the others."""
    store = await bot.open_store()
    boards = await store.run(core.txn_list_boards)
    for channel_id in dict.fromkeys([*core.GAMESTATUS_CHANNELS, *boards]):
        try:
            await publish_board(channel_id, boards.get(channel_id), rows, stamp)
        except ApiError as err:
            if is_token_revoked(err):
                raise
            print(f"game-status: channel {channel_id}: {type(err).__name__}: {err}", file=sys.stderr)


async def seed_servers():
    store = await bot.open_store()
    for server in core.GAMESTATUS_SERVERS:
        await store.run(core.txn_upsert_server, server)


@bot.every(lambda: core.GAMESTATUS_INTERVAL, name="game-status-poll", before=seed_servers)
async def poll_once():
    store = await bot.open_store()
    servers = await store.run(core.txn_list_servers)
    rows = await poll(servers)
    await publish_all(rows, now())


@bot.command(name="gamestatus", help="Show whether each game server is up, with players and map", cooldown=LIST_COOLDOWN_SECONDS, cooldown_bucket="channel")
async def gamestatus(ctx):
    store = await bot.open_store()
    servers = await store.run(core.txn_list_servers)
    rows, stamp = await poll(servers), now()
    await ctx.reply_ephemeral(core.status_text(rows, stamp), embed=core.status_embed(rows, stamp), public_fallback=True)


@bot.group(name="gameserver", help="`add`, `remove`, `list`, `board`, `unboard` (Manage Server)")
async def gameserver(ctx, rest: str = ""):
    p = bot.prefix
    await ctx.reply(style.usage_text(f"{p}gameserver add <kind> <host> <port> <name>", f"{p}gameserver remove <name>", f"{p}gameserver list", f"{p}gameserver board", f"{p}gameserver unboard"))


MANAGE_DENIED = "only someone with Manage Server can change the game server list."


@gameserver.command(name="add", help="Add or replace a server", usage="<a2s|minecraft|tcp> <host> <port> <name>", requires=Permissions.MANAGE_SERVER, requires_message=MANAGE_DENIED)
async def gameserver_add(ctx, kind: str, host: str, port: int, name: str):
    server = {"name": name.strip(), "kind": kind.lower(), "host": host, "port": port}
    problem = core.validate_server(server["name"], server["kind"], host, port)
    if problem:
        await ctx.reply(problem)
        return
    store = await bot.open_store()
    if not await store.run(core.txn_upsert_server, server):
        await ctx.reply(f"at most {core.MAX_SERVERS} servers fit in one status message.")
        return
    await ctx.reply(f"watching `{server['name']}` ({server['kind']} {host}:{port}).")


@gameserver.command(name="remove", help="Stop watching a server", usage="<name>", requires=Permissions.MANAGE_SERVER, requires_message=MANAGE_DENIED)
async def gameserver_remove(ctx, name: str):
    store = await bot.open_store()
    removed = await store.run(core.txn_remove_server, name.strip())
    await ctx.reply(f"stopped watching `{name}`." if removed else f"no server named `{name}`.")


@gameserver.command(name="list", help="List the configured servers", requires=Permissions.MANAGE_SERVER, requires_message=MANAGE_DENIED)
async def gameserver_list(ctx):
    store = await bot.open_store()
    servers = await store.run(core.txn_list_servers)
    lines = [f"`{s['name']}` - {s['kind']} {s['host']}:{s['port']}" for s in servers]
    await ctx.reply("\n".join(lines) or "no servers are configured.")


@gameserver.command(name="board", help="Keep a live status message in this channel", requires=Permissions.MANAGE_SERVER, requires_message=MANAGE_DENIED)
async def gameserver_board(ctx):
    store = await bot.open_store()
    boards = await store.run(core.txn_list_boards)
    rows = await poll(await store.run(core.txn_list_servers))
    await publish_board(ctx.channel_id, boards.get(ctx.channel_id), rows, now())
    await ctx.reply_ephemeral("this channel now has a live status message.", public_fallback=True)


@gameserver.command(name="unboard", help="Stop updating this channel's status message", requires=Permissions.MANAGE_SERVER, requires_message=MANAGE_DENIED)
async def gameserver_unboard(ctx):
    store = await bot.open_store()
    removed = await store.run(core.txn_remove_board, ctx.channel_id)
    await ctx.reply("no longer updating a status message here." if removed else "this channel has no status message.")


if __name__ == "__main__":
    bot.run_cli(check=core.check_config)
