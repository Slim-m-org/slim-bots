#!/usr/bin/env python3
"""bot-cs2-1v1: `!1v1 [map]` starts a throwaway CS2 server in Docker, `!1v1 stop` ends it; see README.md.
Split per docs/framework.md: this is the entry point and the watcher, core.py, dockerapi.py and a2s.py hold the rest."""

import asyncio
import sys
import time

import a2s
import core
from dockerapi import DockerClient, DockerError, ImageMissing
from slimbots import ApiError, Bot
from slimbots.http import is_token_revoked
from slimbots.permissions import Permissions

WATCH_SECONDS = 15
START_COOLDOWN_SECONDS = 10

bot = Bot(prefix="!")
core.configure(bot)

docker = None
servers = {}
query = a2s.query
clock = time.monotonic
start_lock = asyncio.Lock()


def setup_state(docker_client=None):
    """Builds the Docker client once the settings pass; tests hand in a fake."""
    global docker
    docker = docker_client or DockerClient(core.CS2_DOCKER_HOST)


def may_stop(ctx, server):
    return ctx.author.id == server.owner_id or ctx.author.has_permission(Permissions.ADMINISTRATOR)


def where_allowed(ctx):
    return not core.CS2_CHANNEL or ctx.channel_id == core.CS2_CHANNEL


async def say(channel_id, text):
    try:
        await bot.client.send(channel_id, text)
    except ApiError as err:
        if is_token_revoked(err):
            raise
        print(f"{type(err).__name__}: {err}, dropped one post", file=sys.stderr)


async def adopt_existing():
    """After a restart, takes over the containers this bot labelled so they still get watched and stopped."""
    for item in await docker.list_owned():
        if item["Id"] not in servers and item.get("State") in ("running", "created", "restarting"):
            data = await docker.inspect_owned(item["Id"])
            if data is not None:
                servers[item["Id"]] = core.server_from_inspect(data, clock())


async def refresh_known():
    """Drops servers whose container is gone and adopts any this process does not know."""
    live = {item["Id"] for item in await docker.list_owned()}
    for container_id in [key for key in servers if key not in live]:
        gone = servers.pop(container_id)
        await say(gone.channel_id, f"the 1v1 server on `{gone.map_name}` is gone.")
    await adopt_existing()


async def launch(spec, name):
    """Creates and starts the container, pulling the pinned image first when the host lacks it."""
    try:
        container_id = await docker.create(name, spec)
    except ImageMissing:
        await docker.pull(spec["Image"])
        container_id = await docker.create(name, spec)
    await docker.start(container_id)
    return container_id


async def start_server(ctx, requested):
    map_name = core.choose_map(requested)
    if map_name is None:
        await ctx.reply(f"I don't have `{requested[:40]}`. Maps: {', '.join(f'`{m}`' for m in core.CS2_MAPS)}.")
        return
    async with start_lock:
        try:
            await refresh_known()
        except DockerError as err:
            await ctx.reply(f"I can't reach Docker: {err}.")
            return
        if len(servers) >= core.CS2_MAX_SERVERS:
            await ctx.reply(running_text())
            return
        port = core.free_port({s.port for s in servers.values()})
        server = core.Server("", f"cs2-1v1-{port}", port, ctx.author.id, ctx.author.username, ctx.channel_id, map_name, core.new_password(), clock())
        spec = core.container_spec(map_name=map_name, password=server.password, port=port, owner_id=ctx.author.id, owner_name=ctx.author.username, channel_id=ctx.channel_id)
        try:
            server.container_id = await launch(spec, server.name)
        except DockerError as err:
            await ctx.reply(f"I could not start the server: {err}.")
            return
        servers[server.container_id] = server
    await ctx.reply(
        f"starting a 1v1 on `{map_name}`. I will post the connect line when it is up. "
        "The first start downloads the game (about 60 GB) and can take a long time; I will post progress here."
    )


def running_text():
    lines = [f"a 1v1 server is already running on `{s.map_name}`, started by @{s.owner_name}." for s in servers.values()]
    return "\n".join(lines) + f"\nuse `{bot.prefix}1v1 status` to see it, or `{bot.prefix}1v1 stop` if it is yours."


@bot.group(name="1v1", help="Start a private CS2 1v1 server: `1v1 [map]`, `stop`, `status`", suggest={"rest": ("stop", "status", "help")})
async def one_v_one(ctx, rest: str = ""):
    if not where_allowed(ctx):
        await ctx.reply_ephemeral("that is not the channel for this; ask where 1v1 servers are started.", public_fallback=True)
        return
    if rest.strip().lower() == "help":
        await ctx.reply(help_text())
        return
    await start_server(ctx, rest.strip())


def help_text():
    p = bot.prefix
    maps = ", ".join(f"`{m}`" for m in core.CS2_MAPS)
    return (
        f"`{p}1v1 [map]` starts a CS2 1v1 server (maps: {maps}), `{p}1v1 status` shows it, `{p}1v1 stop` ends it (its starter or an admin).\n"
        "It stops by itself after the match or when nobody connects for a while. "
        "The first start downloads the game, which takes a long time."
    )


def status_text(server):
    state = "up" if server.ready_at is not None else "starting"
    line = f"1v1 on `{server.map_name}` ({state}), started by @{server.owner_name}"
    if server.ready_at is not None and not core.CS2_PRIVATE:
        return f"{line}\n`{core.connect_line(server)}`"
    return line


@one_v_one.command(name="status", help="Show the running 1v1 server")
async def status_cmd(ctx):
    try:
        await refresh_known()
    except DockerError as err:
        await ctx.reply(f"I can't reach Docker: {err}.")
        return
    if not servers:
        await ctx.reply("no 1v1 server is running.")
        return
    await ctx.reply("\n".join(status_text(s) for s in servers.values()))
    for server in servers.values():
        if core.CS2_PRIVATE and server.ready_at is not None and server.owner_id == ctx.author.id:
            await ctx.reply_ephemeral(f"`{core.connect_line(server)}`", public_fallback=False)


@one_v_one.command(name="help", help="How to start a 1v1 server")
async def help_cmd(ctx):
    await ctx.reply(help_text())


@one_v_one.command(name="stop", help="Stop your 1v1 server (or any, for an admin)")
async def stop_cmd(ctx):
    try:
        await refresh_known()
    except DockerError as err:
        await ctx.reply(f"I can't reach Docker: {err}.")
        return
    if not servers:
        await ctx.reply("no 1v1 server is running.")
        return
    mine = [s for s in servers.values() if may_stop(ctx, s)]
    if not mine:
        await ctx.reply("only the member who started the server, or an admin, can stop it.")
        return
    for server in mine:
        await finish(server, f"stopped by {ctx.author.mention()}")


async def finish(server, reason):
    """Stops the container and tells the channel; the server leaves the table even when Docker complains."""
    try:
        await docker.stop(server.container_id)
    except DockerError as err:
        print(f"could not stop {server.name}: {err}", file=sys.stderr)
        await say(server.channel_id, f"I could not stop the 1v1 server on `{server.map_name}` ({err}); an admin should check it.")
        return
    servers.pop(server.container_id, None)
    await say(server.channel_id, f"the 1v1 server on `{server.map_name}` is stopped: {reason}.")


async def announce_ready(server):
    if core.CS2_PRIVATE:
        await say(server.channel_id, f"@{server.owner_name} your 1v1 on `{server.map_name}` is up; the connect line is in `{bot.prefix}1v1 status` for you to share.")
        return
    await say(server.channel_id, f"@{server.owner_name} your 1v1 on `{server.map_name}` is up:\n`{core.connect_line(server)}`")


async def report_progress(server, now):
    if now - server.last_progress_at < core.PROGRESS_POST_SECONDS:
        return
    try:
        pct = core.download_progress(await docker.logs(server.container_id))
    except DockerError:
        return
    server.last_progress_at = now
    if pct is not None and pct != server.last_progress_pct:
        server.last_progress_pct = pct
        await say(server.channel_id, f"1v1 server: downloading the game, {pct:.0f}% done.")


async def watch_one(server):
    try:
        info = await asyncio.to_thread(query, server.name, server.port)
    except a2s.QueryError:
        info = None
    now = clock()
    action = core.decide(server, info, now)
    if action == "ready":
        await announce_ready(server)
    elif action is not None:
        await finish(server, action[1])
    elif server.ready_at is None:
        await report_progress(server, now)


@bot.every(WATCH_SECONDS, name="cs2-1v1-watch")
async def watch_once():
    """One pass: forget vanished containers, then query each server and apply the idle and match-over rules."""
    try:
        await refresh_known()
    except DockerError as err:
        print(f"{err}, watching with what I know", file=sys.stderr)
    for server in list(servers.values()):
        await watch_one(server)


def prepare():
    """Builds the run state once the settings pass; returns the settings problem, if any."""
    problem = core.check_config()
    if problem is None:
        setup_state()
    return problem


if __name__ == "__main__":
    bot.run_cli(check=prepare)
