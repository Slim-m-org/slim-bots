#!/usr/bin/env python3
"""bot-feeds: follows RSS, Atom, YouTube, Reddit, Mastodon and Forgejo release feeds into channels; see README.md."""

import asyncio
import functools
import sys
import time
import urllib.parse
import uuid

import db
import parse as parsemod
import source
from slimbots import ApiError, Bot, Duration, Embed, Permissions
from slimbots.http import is_token_revoked

POST_NAMESPACE = uuid.UUID("b2f4e1a6-7c35-4d0e-8a19-6f3c9d2e5b47")
MAX_INTERVAL = 7 * 86400
SUMMARY_LIMIT = 300
LIST_BUDGET = 1800

bot = Bot(prefix="!", default_data_path="feeds.db", store_migrate=db.init_db)

MIN_INTERVAL = max(bot.setting("FEEDS_MIN_INTERVAL", 600, type=int), 60)
DEFAULT_INTERVAL = min(max(bot.setting("FEEDS_DEFAULT_INTERVAL", 1800, type=int), MIN_INTERVAL), MAX_INTERVAL)
MAX_FEEDS = bot.setting("FEEDS_MAX", 50, type=int)
MAX_POSTS_PER_POLL = bot.setting("FEEDS_MAX_POSTS_PER_POLL", 5, type=int)
TICK_SECONDS = bot.setting("FEEDS_TICK_SECONDS", 60, type=int)
ALLOW_PRIVATE = bot.setting("FEEDS_ALLOW_PRIVATE", False, type=bool)

transport = functools.partial(source.web_transport, allow_private=ALLOW_PRIVATE)


class PostFailed(Exception):
    """The channel refused a post; already-posted entries stay marked seen, the rest retry next poll."""


def host_of(url):
    return urllib.parse.urlsplit(url).hostname or "unknown host"


def build_embed(entry, source_name):
    return Embed(
        title=entry.title, description=entry.summary[:SUMMARY_LIMIT] or None, footer=source_name,
        url=entry.link if entry.link.startswith(("http://", "https://")) else None, timestamp=entry.published,
    )


async def post_entry(feed, entry, source_name):
    assert bot.client is not None, "post runs only once connected"
    embed = build_embed(entry, source_name)
    fallback = f"{entry.title}\n{entry.link}".strip()
    try:
        await bot.client.send(
            feed["channel_id"], entry.link or entry.title, message_id=str(uuid.uuid5(POST_NAMESPACE, f"{feed['id']}|{entry.key}")),
            embeds=[embed.to_wire()], fallback_content=fallback,
        )
    except ApiError as err:
        if is_token_revoked(err):
            raise
        raise PostFailed(f"could not post (http {err.status})") from err


def oldest_first(entries):
    if all(e.published is not None for e in entries):
        return sorted(entries, key=lambda e: e.published)
    return list(reversed(entries))


async def deliver(feed, parsed, first):
    """Posts what is new and matches the filter; everything unseen is marked seen so overflow and filtered items never come back."""
    seen = await bot.store.run(db.seen_keys, feed["id"])
    fresh = oldest_first([e for e in parsed.entries if e.key not in seen])
    wanted = [e for e in fresh if db.keyword_match(feed["keywords"], e.title, e.summary)]
    limit = 1 if first else MAX_POSTS_PER_POLL
    name = parsed.title or host_of(feed["url"])
    for entry in wanted[-limit:]:
        await post_entry(feed, entry, name)
        await bot.store.run(db.mark_seen, feed["id"], [entry.key])
    await bot.store.run(db.mark_seen, feed["id"], [e.key for e in fresh])


async def poll_feed(feed, first=False):
    """One fetch and delivery for one feed; returns an error string or None, and never raises except a revoked token."""
    result = await source.fetch(feed["url"], feed["etag"], feed["last_modified"], transport)
    if result.status == "error":
        await bot.store.run(db.record_failure, feed["id"], result.detail, feed["interval"])
        return result.detail
    if result.status == "ok":
        try:
            await deliver(feed, result.parsed, first)
        except PostFailed as err:
            await bot.store.run(db.record_failure, feed["id"], str(err), feed["interval"])
            return str(err)
    await bot.store.run(db.record_success, feed["id"], result.etag or feed["etag"], result.last_modified or feed["last_modified"], feed["interval"])
    return None


async def poll_due():
    for feed in await bot.store.run(db.due_feeds, int(time.time())):
        try:
            await poll_feed(feed)
        except ApiError as err:
            if is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                raise
            await note_unexpected(feed, err)
        except Exception as err:
            await note_unexpected(feed, err)


async def note_unexpected(feed, err):
    print(f"feed {feed['id']} failed: {type(err).__name__}, retrying later", file=sys.stderr)
    await bot.store.run(db.record_failure, feed["id"], f"internal error ({type(err).__name__})", feed["interval"])


async def run_forever():
    while True:
        try:
            await poll_due()
        except ApiError as err:
            if is_token_revoked(err):
                raise
            print(f"{type(err).__name__}: {err}, retrying next tick", file=sys.stderr)
        except Exception as err:
            print(f"{type(err).__name__}: {err}, retrying next tick", file=sys.stderr)
        await asyncio.sleep(TICK_SECONDS)


started = []


@bot.event
async def on_connect():
    if started:
        return
    started.append(True)
    bot.background(run_forever(), name="feeds-poll")


def fmt_interval(seconds):
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{max(seconds // 60, 1)}m"


async def allowed(ctx):
    if ctx.author.has_permission(Permissions.MANAGE_SERVER):
        return True
    await ctx.reply_ephemeral("only someone with Manage Server can change feeds.", public_fallback=True)
    return False


@bot.group(name="feed", help="`add <url> [keywords]`, `list`, `remove <id>`, `filter <id> <keywords|off>`, `interval <id> <duration>`")
async def feed_group(ctx, rest: str = ""):
    p = bot.prefix
    await ctx.reply(f"try `{p}feed add <url>`, `{p}feed list`, `{p}feed remove <id>`, `{p}feed filter <id> <keywords|off>` or `{p}feed interval <id> <10m|2h|1d>`")


@feed_group.command(name="add", help="Follow a feed in this channel", usage="<url> [keywords]")
async def feed_add(ctx, url: str, keywords: str = ""):
    if not await allowed(ctx):
        return
    try:
        feed_url = source.normalize(url)
    except source.SourceError as err:
        await ctx.reply(str(err))
        return
    if len(await bot.store.run(db.list_feeds)) >= MAX_FEEDS:
        await ctx.reply(f"this bot follows at most {MAX_FEEDS} feeds; remove one first.")
        return
    result = await source.fetch(feed_url, None, None, transport)
    if result.status != "ok":
        await ctx.reply(f"could not read that feed: {result.detail}")
        return
    name = result.parsed.title or host_of(feed_url)
    feed_id = await bot.store.run(db.add_feed, ctx.channel_id, feed_url, name, keywords.strip(), DEFAULT_INTERVAL, ctx.author.id)
    if feed_id is None:
        await ctx.reply("this channel already follows that feed.")
        return
    feed = await bot.store.run(db.get_feed, feed_id)
    problem = await finish_add(feed, result)
    tail = f" Posting the latest item failed ({problem}); it retries on the next poll." if problem else ""
    await ctx.reply(f"following **{name}** here as feed `{feed_id}`, checked every {fmt_interval(DEFAULT_INTERVAL)}. Only the latest item is posted now, then new ones as they appear.{tail}")


async def finish_add(feed, result):
    try:
        await deliver(feed, result.parsed, True)
    except PostFailed as err:
        await bot.store.run(db.record_failure, feed["id"], str(err), feed["interval"])
        return str(err)
    await bot.store.run(db.record_success, feed["id"], result.etag, result.last_modified, feed["interval"])
    return None


def describe(feed):
    channel = bot.space.get_channel(feed["channel_id"]) if bot.space else None
    bits = [f"`{feed['id']}` {feed['name']}", f"{host_of(feed['url'])}", f"#{channel.name}" if channel else "unknown channel", f"every {fmt_interval(feed['interval'])}"]
    if feed["keywords"]:
        bits.append(f"filter: {feed['keywords']}")
    if feed["failures"] >= db.FAILING_AFTER:
        bits.append(f"FAILING since {feed['failures']} checks: {feed['last_error']}")
    elif feed["failures"]:
        bits.append(f"last check failed: {feed['last_error']}")
    return " - ".join(bits)


@feed_group.command(name="list", help="Show the followed feeds and any that keep failing")
async def feed_list(ctx):
    feeds = await bot.store.run(db.list_feeds)
    if not feeds:
        await ctx.reply(f"no feeds yet - try `{bot.prefix}feed add <url>`.")
        return
    lines, used = [], 0
    for index, feed in enumerate(feeds):
        line = describe(feed)
        if used + len(line) > LIST_BUDGET:
            lines.append(f"and {len(feeds) - index} more")
            break
        lines.append(line)
        used += len(line) + 1
    await ctx.reply("\n".join(lines))


@feed_group.command(name="remove", help="Stop following a feed", usage="<id>")
async def feed_remove(ctx, feed_id: int):
    if not await allowed(ctx):
        return
    removed = await bot.store.run(db.remove_feed, feed_id)
    await ctx.reply(f"removed feed `{feed_id}`." if removed else f"no feed `{feed_id}`.")


@feed_group.command(name="filter", help="Only post items matching a keyword; `-word` excludes, `off` clears", usage="<id> <keywords|off>")
async def feed_filter(ctx, feed_id: int, keywords: str):
    if not await allowed(ctx):
        return
    value = "" if keywords.strip().lower() == "off" else keywords.strip()
    if not await bot.store.run(db.set_field, feed_id, "keywords", value):
        await ctx.reply(f"no feed `{feed_id}`.")
        return
    await ctx.reply(f"feed `{feed_id}` filter: {value}." if value else f"feed `{feed_id}` filter cleared.")


@feed_group.command(name="interval", help="How often to check a feed", usage="<id> <duration>")
async def feed_interval(ctx, feed_id: int, duration: Duration):
    if not await allowed(ctx):
        return
    seconds = int(duration)
    if not MIN_INTERVAL <= seconds <= MAX_INTERVAL:
        await ctx.reply(f"pick between {fmt_interval(MIN_INTERVAL)} and {fmt_interval(MAX_INTERVAL)}.")
        return
    if not await bot.store.run(db.set_field, feed_id, "interval", seconds):
        await ctx.reply(f"no feed `{feed_id}`.")
        return
    await ctx.reply(f"feed `{feed_id}` is checked every {fmt_interval(seconds)}.")


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
