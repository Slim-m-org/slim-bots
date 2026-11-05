#!/usr/bin/env python3
"""bot-memories: `!onthisday` quotes a public message from months or years ago, `!recap` is a monthly "wrapped"; see README.md."""

from datetime import datetime, timezone

import history
from slimbots import ApiError, Bot

bot = Bot(prefix="!", default_data_path="memories.db")

POST_CHANNEL = bot.setting("MEMORIES_CHANNEL")
POST_HOUR = min(max(bot.setting("MEMORIES_HOUR", 15, type=int), 0), 23)
MONTHLY = bot.setting("MEMORIES_MONTHLY", False, type=bool)
AGES = [a for a in map(history.parse_age, bot.setting("MEMORIES_AGES", "1y,6m,3m,1m", type=list)) if a]
EXTRA_PUBLIC = bot.setting("MEMORIES_CHANNELS", [], type=list)
QUOTE_PAGES = bot.setting("MEMORIES_QUOTE_PAGES", 40, type=int)
RECAP_PAGES = bot.setting("MEMORIES_RECAP_PAGES", 150, type=int)

MONTH_NAMES = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def public_channels():
    """Text channels everyone can read; a channel the server does not call public is skipped unless listed in MEMORIES_CHANNELS."""
    return [
        c for c in bot.space.channels.values()
        if c.kind == "text" and (c.restricted is False or c.id in EXTRA_PUBLIC or c.name in EXTRA_PUBLIC)
    ]


def age_label(months):
    years, rest = divmod(months, 12)
    if rest == 0:
        return "1 year" if years == 1 else f"{years} years"
    return "1 month" if months == 1 else f"{months} months"


def quote_block(text):
    return "\n".join("> " + line for line in text.replace("@", " at ").splitlines() if line.strip())


async def find_memory(today):
    """(age in months, channel name, message) for the first age that has a quotable message, else None."""
    channels = public_channels()
    for months in AGES:
        start_ms, end_ms = history.day_window_ms(history.months_before(today, months))
        candidates = []
        for channel in channels:
            for message in await history.fetch_window(bot.client, channel.id, start_ms, end_ms, QUOTE_PAGES):
                if history.quotable(message, bot.me_id, bot.prefix):
                    candidates.append({**message, "_channel": channel.name})
        chosen = history.pick(candidates, f"{today.isoformat()}:{months}")
        if chosen:
            return months, chosen["_channel"], chosen
    return None


def memory_text(found):
    months, channel, message = found
    return f"On this day {age_label(months)} ago, in #{channel}:\n{quote_block(message['content'])}"


NOTHING_TEXT = "I could not find a quotable message from that date in the public channels."


async def recap_text(year, month):
    start_ms, end_ms = history.month_bounds(year, month)
    per_channel = {c.name: await history.fetch_window(bot.client, c.id, start_ms, end_ms, RECAP_PAGES) for c in public_channels()}
    stats = history.summarise(per_channel)
    title = f"{MONTH_NAMES[month - 1]} {year}"
    if not stats["total"]:
        return f"{title}: nothing was said in the public channels."
    lines = [f"{title} in the public channels", f"- {stats['total']} messages"]
    lines.append(f"- busiest channel: #{stats['busiest']} ({stats['busiest_count']})")
    if stats["busy_day"]:
        lines.append(f"- busiest day: {stats['busy_day'][0]:%B} {stats['busy_day'][0].day} ({stats['busy_day'][1]} messages)")
    if stats["emoji"]:
        lines.append("- most used reactions: " + " ".join(f"{e} x{n}" for e, n in stats["emoji"]))
    if stats["best"]:
        count, channel, message = stats["best"]
        text = (message.get("content") or "").strip()
        if text:
            lines.append(f"- most reacted message ({count} reactions, #{channel}):\n{quote_block(text[:history.MAX_QUOTE])}")
    return "\n".join(lines)


def previous_month(today):
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


def parse_month(text, today):
    if not text:
        return previous_month(today)
    year_text, _, month_text = text.partition("-")
    if year_text.isdigit() and month_text.isdigit() and len(year_text) == 4 and 1 <= int(month_text) <= 12:
        return int(year_text), int(month_text)
    return None


@bot.command(name="onthisday", help="Quote a public message from months or years ago on this date", cooldown=30, cooldown_bucket="deployment")
async def onthisday(ctx):
    async with ctx.typing():
        found = await find_memory(datetime.now(timezone.utc).date())
    await ctx.reply(memory_text(found) if found else NOTHING_TEXT)


@bot.command(name="recap", help="A month in the public channels, with no ranking of people: defaults to last month", usage="[YYYY-MM]", cooldown=20, cooldown_bucket="deployment")
async def recap(ctx, month: str = ""):
    today = datetime.now(timezone.utc).date()
    picked = parse_month(month.strip(), today)
    if picked is None or (picked[0], picked[1]) > (today.year, today.month):
        await ctx.reply_ephemeral(f"give a past month like `{bot.prefix}recap {previous_month(today)[0]}-{previous_month(today)[1]:02d}`.", public_fallback=True)
        return
    async with ctx.typing():
        text = await recap_text(*picked)
    await ctx.reply(text)


async def post_once(kv, key, text_for):
    """Posts to the configured channel at most once per `key`, even when nothing was found."""
    if await kv.get(key):
        return
    await kv.set(key, "1")
    text = await text_for()
    if text:
        await bot.client.send(POST_CHANNEL, text)


@bot.every(300, name="memories", on="ready", enabled=lambda: bool(POST_CHANNEL))
async def daily():
    now = datetime.now(timezone.utc)
    if now.hour < POST_HOUR:
        return
    kv = await (await bot.open_store()).kv("posted")
    today = now.date()

    async def memory():
        found = await find_memory(today)
        return memory_text(found) if found else ""

    try:
        await post_once(kv, f"day:{today.isoformat()}", memory)
        if MONTHLY and today.day == 1:
            year, month = previous_month(today)
            await post_once(kv, f"month:{year}-{month}", lambda: recap_text(year, month))
    except ApiError as err:
        print(f"memories: the post channel refused a message (http {err.status})")


if __name__ == "__main__":
    bot.run_cli()
