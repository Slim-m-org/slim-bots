#!/usr/bin/env python3
"""bot-pelican: `!servers`, `!server <name>`, role-gated power commands, a status message and, as a connector, `/servers` and a panel.
See README.md; this is the entry point and the status loop, pelican_core.py and pelican_panel.py hold the rest."""

import asyncio
import os
import uuid

from slimbots import ApiError, Bot, KeyValueTable, Run, RunError
from slimbots.bot import DEFAULT_CURSOR_DB
from slimbots.http import is_not_found, is_token_revoked

import pelican_core as core
import pelican_panel as panel

STATUS_NAMESPACE = uuid.UUID("70656c69-6361-6e2d-7374-617475732d31")
LIST_COOLDOWN_SECONDS = 10
POWER_COOLDOWN_SECONDS = 15

STATE = KeyValueTable("state")


def init_db(conn):
    STATE.create(conn)
    conn.commit()


bot = Bot(
    prefix="!", default_data_path="pelican.db", store_migrate=init_db,
    cursor_path=os.environ.get("SLIMM_CURSOR_DB") or DEFAULT_CURSOR_DB,
)
core.configure(bot)
_last_status_text = None


async def snapshot(servers):
    """Each server paired with its resource stats, or None where the panel could not answer for that one."""

    async def one(server):
        try:
            return await core.fetch_resources(server["identifier"])
        except core.PelicanAuthError:
            raise
        except core.PelicanError:
            return None

    return list(zip(servers, await asyncio.gather(*(one(server) for server in servers))))


async def resolve(ctx, query):
    """The one server `query` names, or None after replying why not."""
    try:
        servers = await core.list_servers()
    except core.PelicanError as err:
        await ctx.reply(f"can't reach the panel: {err}")
        return None
    matches = core.find_servers(servers, query)
    if not matches:
        await ctx.reply(f"no server matches `{query}` - try `{bot.prefix}servers`.")
        return None
    if len(matches) > 1:
        names = ", ".join(f"`{server['name']}`" for server in matches[:8])
        await ctx.reply(f"`{query}` matches more than one server: {names}. Be more specific.")
        return None
    return matches[0]


@bot.command(name="servers", help="List game servers with state, CPU and RAM", cooldown=LIST_COOLDOWN_SECONDS)
async def servers_cmd(ctx):
    try:
        servers = await core.list_servers()
        rows = await snapshot(servers)
    except core.PelicanError as err:
        await ctx.reply(f"can't reach the panel: {err}")
        return
    if not rows:
        await ctx.reply("the panel has no servers I may show.")
        return
    await ctx.reply("\n".join(core.summary_line(server, stats) for server, stats in rows))


async def panel_rows():
    """Every allowed server with its stats, or a RunError the member reads when the panel cannot answer."""
    try:
        return await snapshot(await core.list_servers())
    except core.PelicanError as err:
        raise RunError(f"can't reach the panel: {err}") from None


@bot.manifest_command("servers")
async def servers_run(run: Run):
    rows = await panel_rows()
    if not rows:
        return "the panel has no servers I may show."
    return "\n".join(core.summary_line(server, stats) for server, stats in rows)


@bot.manifest_command("server-panel")
async def server_panel_run(run: Run):
    return panel.servers_scene(await panel_rows())


@bot.command(name="server", help="Show one game server in detail", usage="<name>", cooldown=LIST_COOLDOWN_SECONDS)
async def server_cmd(ctx, name: str):
    server = await resolve(ctx, name)
    if server is None:
        return
    try:
        stats = await core.fetch_resources(server["identifier"])
    except core.PelicanError as err:
        await ctx.reply(f"can't read `{server['name']}`: {err}")
        return
    await ctx.reply(core.detail_text(server, stats))


def audit_line(ctx, signal, server, outcome):
    return f"pelican power: `{signal}` on `{server['name']}` by {ctx.author.mention()} ({ctx.author.id}) - {outcome}"


async def send_audit(ctx, signal, server, outcome):
    """True once the audit line is posted; a power action never runs without its log entry."""
    try:
        await bot.client.send(core.PELICAN_LOG_CHANNEL, audit_line(ctx, signal, server, outcome))
    except ApiError as err:
        if is_token_revoked(err):
            raise
        return False
    return True


async def power(ctx, signal, query):
    if not core.may_control(ctx.author):
        await ctx.reply(core.control_denied_text())
        return
    if not core.PELICAN_LOG_CHANNEL:
        await ctx.reply("power commands are off until an admin sets PELICAN_LOG_CHANNEL.")
        return
    server = await resolve(ctx, query)
    if server is None:
        return
    if signal != "start" and not await ctx.confirm(f"{signal} `{server['name']}`?"):
        await ctx.reply("cancelled.")
        return
    if not await send_audit(ctx, signal, server, "requested"):
        await ctx.reply("I can't write to the log channel, so I won't run this.")
        return
    try:
        await core.api_power(server["identifier"], signal)
    except core.PelicanError as err:
        await send_audit(ctx, signal, server, f"failed ({err})")
        await ctx.reply(f"`{signal}` on `{server['name']}` failed: {err}")
        return
    await ctx.reply(f"sent `{signal}` to `{server['name']}`.")


@bot.command(name="start", help="Start a game server (control role only)", usage="<name>", cooldown=POWER_COOLDOWN_SECONDS)
async def start_cmd(ctx, name: str):
    await power(ctx, "start", name)


@bot.command(name="stop", help="Stop a game server (control role only)", usage="<name>", cooldown=POWER_COOLDOWN_SECONDS)
async def stop_cmd(ctx, name: str):
    await power(ctx, "stop", name)


@bot.command(name="restart", help="Restart a game server (control role only)", usage="<name>", cooldown=POWER_COOLDOWN_SECONDS)
async def restart_cmd(ctx, name: str):
    await power(ctx, "restart", name)


def status_text(rows):
    if not rows:
        return "**Game servers**\nthe panel has no servers I may show."
    return "**Game servers**\n" + "\n".join(core.summary_line(server, stats) for server, stats in rows)


def status_message_id():
    """The id a status message gets before one is stored, so an existing deployment keeps editing its message."""
    return str(uuid.uuid5(STATUS_NAMESPACE, core.PELICAN_STATUS_CHANNEL))


def _status_key():
    return f"status-message:{core.PELICAN_STATUS_CHANNEL}"


async def publish_status(text):
    """Edits the one status message in place; when it is gone, posts a new one under a fresh stored id.
    A deleted message keeps its id on the server, so reposting under that id would replay the tombstone."""
    global _last_status_text
    if text == _last_status_text:
        return
    store = await bot.open_store(migrate=init_db)
    message_id = await store.run(STATE.get, _status_key()) or status_message_id()
    try:
        await bot.client.edit_message(core.PELICAN_STATUS_CHANNEL, message_id, text)
    except ApiError as err:
        if not is_not_found(err):
            raise
        message_id = str(uuid.uuid4())
        await store.run(_remember, _status_key(), message_id)
        await bot.client.send(core.PELICAN_STATUS_CHANNEL, text, message_id=message_id)
    _last_status_text = text


def _remember(conn, key, value):
    STATE.set(conn, key, value)
    conn.commit()


@bot.every(lambda: core.PELICAN_STATUS_INTERVAL, name="pelican-status", fatal=(core.PelicanAuthError,), enabled=lambda: bool(core.PELICAN_STATUS_CHANNEL))
async def status_once():
    servers = await core.list_servers()
    await publish_status(status_text(await snapshot(servers)))


if __name__ == "__main__":
    bot.run_cli(check=core.check_config)
