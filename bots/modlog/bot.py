#!/usr/bin/env python3
"""bot-modlog: mirrors timeouts, kicks, restores, and role changes into a channel; see README.md."""

import asyncio
import time
import uuid
from datetime import datetime, timezone

from slimbots import ApiError, Bot, style
from slimbots.http import is_forbidden
from slimbots.text import format_duration

import render

# Every gap over the record threshold is kept for `!modlog gaps`; only a longer one is worth a channel post.
GAP_RECORD_THRESHOLD_SECONDS = 5
# Five minutes: shorter drops are gateway blips, and a notice for each buried the log in noise.
GAP_NOTICE_THRESHOLD_SECONDS = 300
# One notice per window: a flapping connection reconnects many times, and each is the same news.
GAP_NOTICE_COOLDOWN_SECONDS = 3600
MAX_GAPS_SHOWN = 10
# A moderation frame at or below the hello head can still land just after it, so a hole is judged only after this wait.
MODERATION_SETTLE_SECONDS = 5
MODERATION_FRAMES = frozenset({"member.timeout", "member.removed", "member.restored", "member.role_changed", "role.changed"})


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts INTEGER NOT NULL,
            kind TEXT NOT NULL,
            text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reconnected_at INTEGER NOT NULL,
            downtime_seconds INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS moderation_state (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            last_seq INTEGER NOT NULL,
            build TEXT
        );
        CREATE TABLE IF NOT EXISTS moderation_gaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            noticed_at INTEGER NOT NULL,
            last_seq INTEGER NOT NULL,
            head INTEGER NOT NULL,
            after_restart INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()


bot = Bot(prefix="!", require_channels=True, default_data_path="modlog.db", store_migrate=init_db)

# user_id -> set of role_ids last observed, used to infer a member.role_changed event's direction.
_last_roles = {}
# role_id -> role name, learned from role events and member profiles.
_role_names = {}
# role_id -> permission bits last read, only while this bot can read the role list.
_role_bits = {}
# Wall-clock time this bot last knew for certain it was connected - None until the first successful connect.
_last_seen_at = None
# The gap notice that is still the newest post in the channel, as [message_id, notices_so_far, total_downtime].
_open_gap_notice: "list | None" = None
# When a gap notice was last posted, for the cooldown.
_last_notice_at = None


def format_until(until_ms):
    """`until` is Unix milliseconds; render the wall-clock time and a human duration together."""
    until_s = until_ms / 1000
    when = datetime.fromtimestamp(until_s, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    remaining = until_s - time.time()
    if remaining <= 0:
        return f"until {when} (already past)"
    return f"until {when} ({format_duration(remaining, "compact")} from now)"


def record_event(conn, kind, text):
    conn.execute("INSERT INTO events (ts, kind, text) VALUES (?, ?, ?)", (int(time.time()), kind, text))
    conn.commit()


async def post(kind, title, fields=(), *, tone="info", description=None):
    """Posts an embed under a fresh id every call - two different events can produce identical text,
    so there is nothing here worth deduplicating against the way a command reply is."""
    global _open_gap_notice
    _open_gap_notice = None
    embed = style.card(tone, title=title, description=description, timestamp=int(time.time() * 1000))
    for name, value in fields:
        embed.add_field(name, value, inline=True)
    text = render.plain_text(title, fields)
    await bot.client.send(bot.channel, text, message_id=str(uuid.uuid4()), **style.send_kwargs(embed, text))
    await bot.store.run(record_event, kind, text)


def note_alive():
    global _last_seen_at
    _last_seen_at = time.time()


def _record_gap(conn, downtime):
    conn.execute("INSERT INTO gaps (reconnected_at, downtime_seconds) VALUES (?, ?)", (int(time.time()), downtime))
    conn.commit()


def _load_moderation_state(conn):
    """(last_seq, build) as persisted, or None before the first connect."""
    return conn.execute("SELECT last_seq, build FROM moderation_state WHERE id = 1").fetchone()


def _load_moderation_seq(conn):
    row = _load_moderation_state(conn)
    return row[0] if row else None


def _store_moderation_seq(conn, seq):
    conn.execute(
        "INSERT INTO moderation_state (id, last_seq) VALUES (1, ?) ON CONFLICT (id) DO UPDATE SET last_seq = MAX(last_seq, excluded.last_seq)",
        (seq,),
    )
    conn.commit()


def _store_build(conn, build):
    conn.execute("UPDATE moderation_state SET build = ? WHERE id = 1", (build,))
    conn.commit()


def _record_moderation_gap(conn, last_seq, head, text, after_restart):
    conn.execute(
        "INSERT INTO moderation_gaps (noticed_at, last_seq, head, after_restart) VALUES (?, ?, ?, ?)",
        (int(time.time()), last_seq, head, int(after_restart)),
    )
    conn.execute("INSERT INTO events (ts, kind, text) VALUES (?, 'gap', ?)", (int(time.time()), text))
    conn.commit()


async def note_moderation_seq(frame):
    seq = frame.get("seq")
    if frame.get("type") in MODERATION_FRAMES and isinstance(seq, int):
        await bot.store.run(_store_moderation_seq, seq)


async def current_build():
    """The server's identity from /version (version, capabilities and the git build_id when it sends one); None when unreadable."""
    try:
        info = await bot.client.call("GET", "/version")
    except ApiError:
        return None
    if not isinstance(info, dict) or "version" not in info:
        return None
    build = f"{info['version']}+{','.join(sorted(info.get('capabilities') or []))}"
    build_id = info.get("build_id")
    return f"{build}@{build_id}" if isinstance(build_id, str) and build_id else build


async def confirm_moderation_gap(last_seq, head, after_restart):
    """Logs a marker when, after the settle wait, the hello head is still ahead of every moderation event received."""
    await asyncio.sleep(MODERATION_SETTLE_SECONDS)
    received = await bot.store.run(_load_moderation_seq)
    if received >= head:
        return
    if after_restart:
        text = "server restarted, events may have been missed"
    else:
        text = (
            "moderation events were missed on an unchanged server build: the moderation cursor is ahead of "
            "the last event this bot recorded, and nothing can replay them"
        )
    await bot.store.run(_record_moderation_gap, received, head, text, after_restart)
    await bot.store.run(_store_moderation_seq, head)
    print(f"gap marker: {text} (last seen {last_seq}, server head {head})", flush=True)


async def check_moderation_cursor():
    """Compares the hello's moderation head with the persisted seq; the first connect only seeds seq and build."""
    head = bot.moderation_head
    if head is None:
        return
    state = await bot.store.run(_load_moderation_state)
    build = await current_build()
    if state is None:
        await bot.store.run(_store_moderation_seq, head)
    elif head > state[0]:
        after_restart = build is not None and state[1] is not None and build != state[1]
        bot.background(confirm_moderation_gap(state[0], head, after_restart), name="modlog-moderation-gap")
    if build is not None:
        await bot.store.run(_store_build, build)


def gap_notice_text(count, downtime):
    total = "" if count == 1 else " in total"
    return f"Mod log was offline for about {format_duration(downtime, "compact")}{total}; events in that time were not recorded."


async def report_reconnect_gap():
    """Records downtime since the last frame seen; posts a notice only for a long gap, at most once per cooldown, editing it in place if nothing was posted since."""
    global _last_seen_at, _open_gap_notice, _last_notice_at
    assert bot.client is not None and bot.channel is not None, "report_reconnect_gap runs only once connected"
    client, channel = bot.client, bot.channel
    if _last_seen_at is None:
        return
    downtime = int(time.time() - _last_seen_at)
    if downtime < GAP_RECORD_THRESHOLD_SECONDS:
        return
    await bot.store.run(_record_gap, downtime)
    if downtime < GAP_NOTICE_THRESHOLD_SECONDS:
        return
    if _open_gap_notice is not None:
        message_id, count, total = _open_gap_notice
        count, total = count + 1, total + downtime
        try:
            await client.edit_message(channel, message_id, gap_notice_text(count, total))
        except ApiError:
            _open_gap_notice = None
        else:
            _open_gap_notice = [message_id, count, total]
            return
    if _last_notice_at is not None and time.time() - _last_notice_at < GAP_NOTICE_COOLDOWN_SECONDS:
        return
    text = gap_notice_text(1, downtime)
    message_id = str(uuid.uuid4())
    await client.send(channel, text, message_id=message_id)
    await bot.store.run(record_event, "gap", text)
    _open_gap_notice = [message_id, 1, downtime]
    _last_notice_at = time.time()


async def resolve_member(user_id):
    """Fetches a member's current roles, seeding the role-name map; None if the account is gone outright."""
    try:
        member = await bot.space.fetch_member(user_id)
    except ApiError as err:
        if err.status == 404:
            return None
        raise
    for role_id, name in zip(member.role_ids, member.roles):
        _role_names[role_id] = name
    return member


def member_label(user_id):
    """`Nick (@nick)` for a member the Space knows, else a plain stand-in; never the id."""
    return render.person(bot.space.members.get(user_id))


def role_name_of(role_id):
    return _role_names.get(role_id)


async def handle_role_change(frame):
    """The frame says grant or revoke itself on a current server; an older one is inferred from the member's role set."""
    user_id, role_id = frame["user_id"], frame["role_id"]
    member = await resolve_member(user_id)
    current = set(member.role_ids) if member else set()
    previous = _last_roles.get(user_id)
    _last_roles[user_id] = current
    if frame.get("role_name"):
        _role_names[role_id] = frame["role_name"]
    granted = frame.get("granted")
    if granted is None and previous is not None and (role_id in current) != (role_id in previous):
        granted = role_id in current
    role = role_name_of(role_id) or "Unknown role"
    fields = [("Member", member_label(user_id)), ("Role", role)]
    if granted is None:
        await post("member.role_changed", "Member roles changed", fields)
    elif granted:
        await post("member.role_changed", "Role granted", fields, tone="success")
    else:
        await post("member.role_changed", "Role revoked", fields, tone="warn")


async def read_role_bits():
    """role_id -> permission bits while this bot may list roles, else None (a missing right and a failed call read the same)."""
    try:
        roles = await bot.client.call("GET", "/roles")
    except ApiError:
        return None
    return {role["id"]: role["permissions"] for role in roles}


async def role_permission_line(role_id, change):
    """The Permissions field text for this role change, or None when this bot cannot see the bits."""
    global _role_bits
    before = _role_bits.get(role_id)
    current = await read_role_bits()
    if current is None:
        return None
    _role_bits = current
    after = current.get(role_id)
    if change == "created" and after is not None:
        return ", ".join(render.permission_names(after)) or "None"
    if change == "deleted" or before is None or after is None:
        return None
    return render.permission_diff(before, after)


def _fetch_stats(conn):
    return conn.execute("SELECT kind, COUNT(*) FROM events WHERE kind != 'gap' GROUP BY kind ORDER BY kind").fetchall()


async def show_stats(ctx):
    rows = await bot.store.run(_fetch_stats)
    if not rows:
        await ctx.reply("nothing recorded yet.")
        return
    total = sum(count for _, count in rows)
    lines = [f"{total} event(s) recorded locally since this bot's database was created:"]
    lines.extend(f"- {kind}: {count}" for kind, count in rows)
    lines.append("this is only what this bot itself saw live, never a substitute for a real audit trail.")
    await ctx.reply("\n".join(lines))


def _fetch_gaps(conn):
    return conn.execute(
        "SELECT reconnected_at, downtime_seconds FROM gaps ORDER BY id DESC LIMIT ?", (MAX_GAPS_SHOWN,)
    ).fetchall()


def _count_moderation_gaps(conn):
    """(plain gaps, gaps after a server restart)."""
    return conn.execute(
        "SELECT COALESCE(SUM(1 - after_restart), 0), COALESCE(SUM(after_restart), 0) FROM moderation_gaps"
    ).fetchone()


async def show_gaps(ctx):
    rows = await bot.store.run(_fetch_gaps)
    plain_gaps, restart_gaps = await bot.store.run(_count_moderation_gaps)
    if not rows and not plain_gaps and not restart_gaps:
        await ctx.reply("no reconnect gaps recorded.")
        return
    lines = [f"last {len(rows)} known gap(s), most recent first:"] if rows else []
    for reconnected_at, downtime in rows:
        when = datetime.fromtimestamp(reconnected_at, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines.append(f"- reconnected {when} after ~{format_duration(downtime, "compact")} offline")
    if plain_gaps:
        lines.append(f"the moderation cursor was ahead of this log {plain_gaps} time(s) on an unchanged server build, so events were missed then.")
    if restart_gaps:
        lines.append(f"{restart_gaps} more time(s) it was ahead after the server restarted, so events may have been missed.")
    lines.append("moderation events during any of these gaps are permanently missing from this log.")
    await ctx.reply("\n".join(lines))


async def show_permissions(ctx):
    await ctx.reply(
        "I hold only View channels and Send messages on this channel. Two more would change what I can do:\n"
        "- Manage messages: I could read the reason behind a timeout or removal, and fill in a reconnect gap "
        "instead of leaving it permanent.\n"
        "- Manage roles: I could show what a role's permissions changed, not only its name and look."
    )


@bot.command(name="modlog", help="Show stats, gaps or permissions from this bot's own local transcript", usage="<stats|gaps|permissions>", choices={"sub": ("stats", "gaps", "permissions")})
async def modlog_cmd(ctx, sub: str):
    sub = sub.lower()
    if sub == "stats":
        await show_stats(ctx)
    elif sub == "gaps":
        await show_gaps(ctx)
    else:
        await show_permissions(ctx)


@bot.event
async def on_frame(frame):
    note_alive()
    await note_moderation_seq(frame)


@bot.event
async def on_ready():
    global _role_bits
    _role_bits = await read_role_bits() or {}
    await check_moderation_cursor()


@bot.event
async def on_member_timeout(frame):
    user_id = frame["user_id"]
    await resolve_member(user_id)
    until = frame.get("until")
    fields = [("Member", member_label(user_id))]
    if until is None:
        await post("member.timeout", "Timeout lifted", fields, tone="success")
    else:
        await post("member.timeout", "Member timed out", [*fields, ("Until", format_until(until))], tone="warn")


@bot.event
async def on_member_removed(frame):
    user_id = frame["user_id"]
    await resolve_member(user_id)
    await post("member.removed", "Member removed", [("Member", member_label(user_id))], tone="error")


@bot.event
async def on_member_restored(frame):
    user_id = frame["user_id"]
    await resolve_member(user_id)
    await post("member.restored", "Member restored", [("Member", member_label(user_id))], tone="success")


@bot.event
async def on_member_role_changed(frame):
    await handle_role_change(frame)


ROLE_TITLES = {"created": ("Role created", "success"), "deleted": ("Role deleted", "error")}


@bot.event
async def on_role_changed(frame):
    role_id, change = frame["role_id"], frame.get("change")
    name = frame.get("name") or role_name_of(role_id)
    if name and change != "deleted":
        _role_names[role_id] = name
    title, tone = ROLE_TITLES.get(change, ("Role changed", "info"))
    fields = [("Role", name or "Unknown role")]
    fields += render.role_look_diff(frame.get("before"), frame.get("after"))
    permissions = await role_permission_line(role_id, change)
    if permissions:
        fields.append(("Permissions", permissions))
    description = None if change or len(fields) > 1 else "Its position or access was adjusted."
    await post("role.changed", title, fields, tone=tone, description=description)


async def announce_catchup_capability():
    """Proves, out loud, whether this bot could ever backfill a reconnect gap - rather than silently having no opinion."""
    try:
        await bot.client.call("GET", "/reports/history?limit=1")
        print("catch-up available: /reports/history is readable", flush=True)
    except ApiError as err:
        if not is_forbidden(err):
            raise
        print(
            "no catch-up available: /reports/history needs MANAGE_MESSAGES, which this bot does not hold - "
            "a reconnect gap in the moderation log is permanent",
            flush=True,
        )


@bot.event
async def on_connect():
    await announce_catchup_capability()
    await report_reconnect_gap()
    note_alive()


if __name__ == "__main__":
    bot.run_cli()
