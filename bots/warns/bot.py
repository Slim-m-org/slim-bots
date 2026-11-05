#!/usr/bin/env python3
"""bot-warns: strike history per member with thresholds that escalate to a timeout or a removal; see README.md."""

import time

from slimbots import ApiError, Bot, Duration, Member, Permissions, style
from slimbots.http import is_forbidden

MAX_REASON = 300
MAX_SHOWN = 15
MAX_RULES = 10
SECONDS_PER_DAY = 86400


def init_db(conn):
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS warns (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL, moderator_id TEXT NOT NULL,
            reason TEXT NOT NULL, created_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS warns_by_user ON warns (user_id, created_at);
        CREATE TABLE IF NOT EXISTS rules (
            warn_count INTEGER PRIMARY KEY, action TEXT NOT NULL, seconds INTEGER NOT NULL DEFAULT 0
        );
        """
    )
    conn.commit()


bot = Bot(prefix="!", default_data_path="warns.db", store_migrate=init_db)

LOG_CHANNEL = bot.setting("WARNS_LOG_CHANNEL", "")
EXPIRE_DAYS = bot.setting("WARNS_EXPIRE_DAYS", 0, type=int)

NEEDS_KICK = "only someone with Kick Members can do that."
NEEDS_SERVER = "only someone with Manage Server can change the escalation rules."


def clean(text):
    """Keeps a reason from pinging anyone: an at-sign in typed text never reaches a channel."""
    return text.replace("@", " at ")


def _cutoff():
    return int(time.time()) - EXPIRE_DAYS * SECONDS_PER_DAY if EXPIRE_DAYS > 0 else 0


def add_warn(conn, user_id, moderator_id, reason):
    cur = conn.execute(
        "INSERT INTO warns (user_id, moderator_id, reason, created_at) VALUES (?, ?, ?, ?)",
        (user_id, moderator_id, reason, int(time.time())),
    )
    conn.commit()
    return cur.lastrowid


def active_warns(conn, user_id):
    return conn.execute(
        "SELECT id, moderator_id, reason, created_at FROM warns WHERE user_id = ? AND created_at >= ? ORDER BY id",
        (user_id, _cutoff()),
    ).fetchall()


def delete_warn(conn, warn_id):
    cur = conn.execute("DELETE FROM warns WHERE id = ?", (warn_id,))
    conn.commit()
    return cur.rowcount > 0


def clear_warns(conn, user_id):
    cur = conn.execute("DELETE FROM warns WHERE user_id = ?", (user_id,))
    conn.commit()
    return cur.rowcount


def rule_at(conn, warn_count):
    return conn.execute("SELECT action, seconds FROM rules WHERE warn_count = ?", (warn_count,)).fetchone()


def all_rules(conn):
    return conn.execute("SELECT warn_count, action, seconds FROM rules ORDER BY warn_count").fetchall()


def set_rule(conn, warn_count, action, seconds):
    conn.execute(
        "INSERT INTO rules (warn_count, action, seconds) VALUES (?, ?, ?) "
        "ON CONFLICT (warn_count) DO UPDATE SET action = excluded.action, seconds = excluded.seconds",
        (warn_count, action, seconds),
    )
    conn.commit()


def delete_rule(conn, warn_count):
    cur = conn.execute("DELETE FROM rules WHERE warn_count = ?", (warn_count,))
    conn.commit()
    return cur.rowcount > 0


def describe_rule(action, seconds):
    if action == "kick":
        return "remove them from the space"
    return f"time them out for {style_duration(seconds)}"


def style_duration(seconds):
    for unit, size in (("d", SECONDS_PER_DAY), ("h", 3600), ("m", 60)):
        if seconds % size == 0 and seconds >= size:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def parse_action(tokens):
    """(action, seconds) from `timeout <duration>` or `kick`, or None for anything else."""
    if tokens[:1] == ["kick"] and len(tokens) == 1:
        return "kick", 0
    if tokens[:1] == ["timeout"] and len(tokens) == 2:
        seconds = Duration.parse(tokens[1].lower())
        if seconds:
            return "timeout", int(seconds)
    return None


async def write_log(text):
    if not LOG_CHANNEL:
        return
    try:
        await bot.client.send(LOG_CHANNEL, text)
    except ApiError as err:
        print(f"warns: the log channel refused a post (http {err.status})")


async def escalate(member, count):
    """Runs the rule set for exactly this many warns; returns a sentence about what happened, or '' for no rule."""
    rule = await bot.store.run(rule_at, count)
    if rule is None:
        return ""
    action, seconds = rule
    try:
        if action == "kick":
            await bot.client.call("PUT", f"/members/{member.id}/removal", {"reason": f"warns: {count} warns"})
        else:
            await bot.client.time_out_member(member.id, seconds, reason=f"warns: {count} warns")
    except ApiError as err:
        need = "Ban Members" if action == "kick" else "Kick Members"
        cause = f"I need {need} for that" if is_forbidden(err) else f"the server refused it (http {err.status})"
        return f"At {count} warns the rule says to {describe_rule(action, seconds)}, but {cause}."
    return f"At {count} warns: {describe_rule(action, seconds)}."


@bot.command(name="warn", help="Give a member a warn; a rule may time them out or remove them", usage="<@member> [reason]", requires=Permissions.KICK_MEMBERS, requires_message=NEEDS_KICK)
async def warn(ctx, member: Member, reason: str = ""):
    if member.id == ctx.author.id or member.is_bot:
        await ctx.reply_ephemeral("you can warn another person, not yourself or a bot.", public_fallback=True)
        return
    reason = clean(reason.strip())[:MAX_REASON] or "no reason given"
    store = bot.store
    await store.run(add_warn, member.id, ctx.author.id, reason)
    count = len(await store.run(active_warns, member.id))
    outcome = await escalate(member, count)
    line = f"{ctx.author.mention()} warned {member.mention()} ({count} active): {reason}"
    await write_log(line + (f"\n{outcome}" if outcome else ""))
    await ctx.reply(f"{member.mention()} has {count} active warn{'s' if count != 1 else ''}." + (f" {outcome}" if outcome else ""))


@bot.command(name="warns", help="A member's warns; you can always see your own", usage="[@member]")
async def warns(ctx, member: Member = None):
    target = member or ctx.author
    if target.id != ctx.author.id and not ctx.author.has_permission(Permissions.KICK_MEMBERS):
        await ctx.reply_ephemeral(NEEDS_KICK, public_fallback=True)
        return
    rows = await bot.store.run(active_warns, target.id)
    if not rows:
        await ctx.reply_ephemeral(f"{target.display_name} has no active warns.", public_fallback=True)
        return
    lines = [f"{target.display_name} has {len(rows)} active warn{'s' if len(rows) != 1 else ''}:"]
    for warn_id, _moderator, reason, created in rows[-MAX_SHOWN:]:
        lines.append(f"- #{warn_id} {time.strftime('%Y-%m-%d', time.gmtime(created))}: {reason}")
    if len(rows) > MAX_SHOWN:
        lines.append(f"(the latest {MAX_SHOWN} of {len(rows)})")
    await ctx.reply_ephemeral("\n".join(lines), public_fallback=True)


@bot.command(name="unwarn", help="Remove one warn by its number, or all of a member's with `clear @member`", usage="<number> | clear <@member>", requires=Permissions.KICK_MEMBERS, requires_message=NEEDS_KICK)
async def unwarn(ctx, first: str, member: Member = None):
    if first.lower() == "clear" and member is not None:
        removed = await bot.store.run(clear_warns, member.id)
        await write_log(f"{ctx.author.mention()} cleared {removed} warn(s) for {member.mention()}")
        await ctx.reply_ephemeral(f"cleared {removed} warn{'s' if removed != 1 else ''} for {member.display_name}.", public_fallback=True)
        return
    if not first.isdigit():
        await ctx.reply_ephemeral(style.usage_text(f"{bot.prefix}unwarn <number>", f"{bot.prefix}unwarn clear @member"), public_fallback=True)
        return
    if not await bot.store.run(delete_warn, int(first)):
        await ctx.reply_ephemeral(f"there is no warn #{first}.", public_fallback=True)
        return
    await write_log(f"{ctx.author.mention()} removed warn #{first}")
    await ctx.reply_ephemeral(f"removed warn #{first}.", public_fallback=True)


@bot.group(name="warnrule", help="What happens at a number of warns: `add`, `remove`, `list`")
async def warnrule(ctx, rest: str = ""):
    await _rules_listing(ctx)


async def _rules_listing(ctx):
    rules = await bot.store.run(all_rules)
    p = bot.prefix
    if not rules:
        await ctx.reply_ephemeral(f"no rules yet; warns only add up. Try `{p}warnrule add 3 timeout 1h`.", public_fallback=True)
        return
    await ctx.reply_ephemeral("\n".join(f"- {n} warns: {describe_rule(a, s)}" for n, a, s in rules), public_fallback=True)


@warnrule.command(name="list", help="The escalation rules")
async def warnrule_list(ctx):
    await _rules_listing(ctx)


@warnrule.command(name="add", help="Act when a member reaches a number of warns", usage="<count> timeout <duration> | <count> kick", requires=Permissions.MANAGE_SERVER, requires_message=NEEDS_SERVER)
async def warnrule_add(ctx, args: str = ""):
    tokens = args.split()
    action = parse_action(tokens[1:]) if tokens and tokens[0].isdigit() and int(tokens[0]) > 0 else None
    if action is None:
        p = bot.prefix
        await ctx.reply_ephemeral(style.usage_text(f"{p}warnrule add 3 timeout 1h", f"{p}warnrule add 5 kick"), public_fallback=True)
        return
    if len(await bot.store.run(all_rules)) >= MAX_RULES and await bot.store.run(rule_at, int(tokens[0])) is None:
        await ctx.reply_ephemeral(f"keep it to {MAX_RULES} rules; remove one first.", public_fallback=True)
        return
    await bot.store.run(set_rule, int(tokens[0]), *action)
    await ctx.reply_ephemeral(f"at {int(tokens[0])} warns: {describe_rule(*action)}.", public_fallback=True)


@warnrule.command(name="remove", help="Drop the rule at a number of warns", usage="<count>", requires=Permissions.MANAGE_SERVER, requires_message=NEEDS_SERVER)
async def warnrule_remove(ctx, count: str = ""):
    if not count.isdigit() or not await bot.store.run(delete_rule, int(count)):
        await ctx.reply_ephemeral(f"there is no rule at {clean(count)[:20] or 'that'} warns.", public_fallback=True)
        return
    await ctx.reply_ephemeral(f"removed the rule at {int(count)} warns.", public_fallback=True)


if __name__ == "__main__":
    bot.run_cli()
