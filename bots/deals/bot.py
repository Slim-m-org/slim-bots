#!/usr/bin/env python3
"""bot-deals: posts free-to-keep games from Epic, Steam and GOG and pings members when a watched Steam game hits their price; see README.md."""

import asyncio
import sys
import time
import uuid

import db
import prices
import sources
from slimbots import ApiError, Bot, Embed, Permissions
from slimbots.http import is_token_revoked

POST_NAMESPACE = uuid.UUID("5d0c8f1e-3a47-4b62-9e15-c7a2f4d6b983")
MAX_POSTS_PER_RUN = 10
MAX_WATCHES_PER_USER = 20

bot = Bot(prefix="!", default_data_path="deals.db", store_migrate=db.init_db)

FREEBIES_INTERVAL = max(bot.setting("DEALS_FREEBIES_INTERVAL", 10800, type=int), 900)
WATCH_INTERVAL = max(bot.setting("DEALS_WATCH_INTERVAL", 86400, type=int), 3600)
STEAM_CC = bot.setting("DEALS_CC", "us").lower()
STEAM_DELAY = bot.setting("DEALS_STEAM_DELAY", 1.5, type=float)
DEFAULT_SOURCES = [s for s in bot.setting("DEALS_SOURCES", "epic,steam,gog").replace(" ", "").split(",") if s in sources.ALL_SOURCES] or list(sources.ALL_SOURCES)
TICK_SECONDS = bot.setting("DEALS_TICK_SECONDS", 60, type=int)

transport = sources.urllib_transport


def clean(text):
    """Keeps a title from pinging anyone: an at-sign in outside text never reaches the channel."""
    return text.replace("@", " at ")


def ends_text(offer):
    return f"ends {offer.ends_at:%Y-%m-%d %H:%M} UTC" if offer.ends_at else "no end date given"


def offer_embed(offer):
    detail = f"Free to keep on {sources.STORE_NAMES[offer.store]}, {ends_text(offer)}."
    return Embed(title=clean(offer.title), description=detail + (f" Normally {offer.worth}." if offer.worth else ""), url=offer.url, footer=sources.STORE_NAMES[offer.store])


def offer_fallback(offer):
    return f"{clean(offer.title)} - free to keep on {sources.STORE_NAMES[offer.store]}, {ends_text(offer)}\n{offer.url}"


async def post(channel_id, content, *, message_id, embed=None, fallback=None):
    assert bot.client is not None, "post runs only once connected"
    try:
        await bot.client.send(
            channel_id, content, message_id=message_id,
            embeds=[embed.to_wire()] if embed else None, fallback_content=fallback,
        )
    except ApiError as err:
        if is_token_revoked(err):
            raise
        print(f"deals: could not post (http {err.status})", file=sys.stderr)
        return False
    return True


async def configured_channel():
    return await bot.store.run(db.get_config, "channel_id")


async def active_sources():
    stored = await bot.store.run(db.get_config, "sources")
    return [s for s in stored.split(",") if s] if stored else list(DEFAULT_SOURCES)


async def check_freebies():
    """Posts each unseen live offer once; a refused post stays unseen and retries next run. Returns (posted, errors)."""
    channel_id = await configured_channel()
    if not channel_id:
        return 0, {}
    offers, errors = await asyncio.to_thread(sources.fetch_offers, await active_sources(), transport, STEAM_CC.upper())
    posted = 0
    for offer in offers:
        if posted >= MAX_POSTS_PER_RUN or await bot.store.run(db.is_seen, offer.store, offer.offer_id):
            continue
        message_id = str(uuid.uuid5(POST_NAMESPACE, f"{offer.store}|{offer.offer_id}"))
        if await post(channel_id, offer.url, message_id=message_id, embed=offer_embed(offer), fallback=offer_fallback(offer)):
            await bot.store.run(db.mark_seen, offer.store, offer.offer_id, offer.title)
            posted += 1
    await bot.store.run(db.set_config, "last_freebies_at", int(time.time()))
    return posted, errors


def store_link(app_id):
    return f"https://store.steampowered.com/app/{app_id}/"


async def mention_for(user_id):
    member = await bot.space.find_member(user_id)
    return member.mention() if member else None


async def notify_price(channel_id, watch, quote, price):
    who = await mention_for(watch["user_id"])
    if who is None:
        return False
    name, target = clean(quote.name or watch["name"]), prices.format_cents(watch["target_price_cents"], watch["currency"])
    now = "free" if price == 0 else f"{prices.format_cents(price, watch['currency'])}" + (f" ({quote.discount_percent}% off)" if quote.discount_percent else "")
    text = f"{who} **{name}** is {now}, at or under your target of {target}.\n{store_link(watch['app_id'])}"
    return await post(channel_id, text, message_id=str(uuid.uuid4()))


async def notify_missing(channel_id, watch):
    who = await mention_for(watch["user_id"])
    if who is None:
        return
    text = f"{who} **{clean(watch['name'])}** has not been available in the {STEAM_CC.upper()} store for {db.MISSING_AFTER} checks in a row. The watch stays until you run `{bot.prefix}watch remove {watch['id']}`."
    await post(channel_id, text, message_id=str(uuid.uuid4()))


async def apply_quote(channel_id, watch, quote):
    if quote.status == "error":
        return
    if quote.status == "unavailable":
        if await bot.store.run(db.record_miss, watch["id"]) == db.MISSING_AFTER:
            await notify_missing(channel_id, watch)
        return
    price = 0 if quote.status == "free" else quote.cents
    if quote.status == "ok" and quote.currency != watch["currency"]:
        return
    notified = watch["notified_price_cents"] if price <= watch["target_price_cents"] else None
    sent = False
    if prices.should_notify(price, watch["target_price_cents"], watch["notified_price_cents"]):
        sent = await notify_price(channel_id, watch, quote, price)
        notified = price if sent else watch["notified_price_cents"]
    await bot.store.run(db.record_price, watch["id"], price, quote.name or watch["name"], notified, sent)


async def check_watches():
    """One appdetails lookup per distinct app, spaced out for Steam's rate limit; returns how many watches were checked."""
    channel_id = await configured_channel()
    watches = await bot.store.run(db.list_watches)
    if not channel_id or not watches:
        return 0
    quotes = {}
    for watch in watches:
        if watch["app_id"] not in quotes:
            if quotes:
                await asyncio.sleep(STEAM_DELAY)
            quotes[watch["app_id"]] = await asyncio.to_thread(prices.fetch_quote, transport, watch["app_id"], STEAM_CC)
        await apply_quote(channel_id, watch, quotes[watch["app_id"]])
    await bot.store.run(db.set_config, "last_watches_at", int(time.time()))
    return len(watches)


async def due(key, interval):
    last = int(await bot.store.run(db.get_config, key, 0))
    return time.time() - last >= interval


async def run_forever():
    while True:
        try:
            if await due("last_freebies_at", FREEBIES_INTERVAL):
                await check_freebies()
            if await due("last_watches_at", WATCH_INTERVAL):
                await check_watches()
        except Exception as err:
            if isinstance(err, ApiError) and is_token_revoked(err):
                print("slimm bot token rejected - exiting", file=sys.stderr)
                raise
            print(f"deals: {type(err).__name__}, retrying next tick", file=sys.stderr)
        await asyncio.sleep(TICK_SECONDS)


started = []


@bot.event
async def on_connect():
    if started:
        return
    started.append(True)
    bot.background(run_forever(), name="deals-loop")


async def allowed(ctx):
    if ctx.author.has_permission(Permissions.MANAGE_SERVER):
        return True
    await ctx.reply_ephemeral("only someone with Manage Server can change the deals settings.", public_fallback=True)
    return False


@bot.group(name="deals", help="`setup`, `sources <list>`, `now`")
async def deals_group(ctx, rest: str = ""):
    p = bot.prefix
    await ctx.reply(f"try `{p}deals setup` in the channel for free games, `{p}deals sources epic,steam,gog` or `{p}deals now`. Price watches: `{p}watch <steam link or app id> <target price>`.")


@deals_group.command(name="setup", help="Post free games and watch alerts in this channel")
async def deals_setup(ctx):
    if not await allowed(ctx):
        return
    await bot.store.run(db.set_config, "channel_id", ctx.channel_id)
    await bot.store.run(db.set_config, "last_freebies_at", 0)
    await ctx.reply(f"free games and price alerts go to this channel now; the first check runs within {max(TICK_SECONDS, 1)} seconds, then every {FREEBIES_INTERVAL // 3600 or 1}h.")


@deals_group.command(name="sources", help="Choose where free games come from", usage="<epic,steam,gog>")
async def deals_sources(ctx, names: str = ""):
    if not await allowed(ctx):
        return
    chosen = [n for n in names.lower().replace(" ", ",").split(",") if n]
    bad = [n for n in chosen if n not in sources.ALL_SOURCES]
    if bad or not chosen:
        await ctx.reply(f"pick from {', '.join(sources.ALL_SOURCES)}, comma separated. Now: {', '.join(await active_sources())}.")
        return
    chosen = list(dict.fromkeys(chosen))
    await bot.store.run(db.set_config, "sources", ",".join(chosen))
    await ctx.reply(f"checking {', '.join(chosen)}.")


@deals_group.command(name="now", help="Check for free games and price drops right away", cooldown=60, cooldown_bucket="deployment")
async def deals_now(ctx):
    if not await allowed(ctx):
        return
    if not await configured_channel():
        await ctx.reply(f"run `{bot.prefix}deals setup` in the deals channel first.")
        return
    posted, errors = await check_freebies()
    checked = await check_watches()
    problems = "".join(f" {sources.STORE_NAMES[n]} failed: {why}." for n, why in errors.items())
    await ctx.reply_ephemeral(f"posted {posted} new free game(s), checked {checked} watch(es).{problems}", public_fallback=True)


def quote_problem(quote, app_id):
    if quote.status == "unavailable":
        return f"Steam has no priced game with id {app_id} in the {STEAM_CC.upper()} store (it may be region locked or removed)."
    if quote.status == "free":
        return f"{clean(quote.name)} is free to play, so there is no price to watch."
    if quote.status == "error":
        return f"could not reach Steam ({quote.detail}); try again in a minute."
    return None


@bot.group(name="pricewatch", help="`<steam link or app id> <target price>`, `list`, `remove <id>`", cooldown=3)
async def watch_group(ctx, rest: str = ""):
    target, _, price = rest.strip().partition(" ")
    if not target or not price.strip():
        await ctx.reply_ephemeral(f"usage: `{bot.prefix}watch <steam link or app id> <target price>`, `{bot.prefix}watch list` or `{bot.prefix}watch remove <id>`.", public_fallback=True)
        return
    try:
        app_id, target_cents = prices.parse_app_id(target), prices.parse_price_cents(price)
    except prices.PriceError as err:
        await ctx.reply_ephemeral(str(err), public_fallback=True)
        return
    if await bot.store.run(db.count_watches, ctx.author.id) >= MAX_WATCHES_PER_USER:
        await ctx.reply_ephemeral(f"you can watch at most {MAX_WATCHES_PER_USER} games; remove one first.", public_fallback=True)
        return
    quote = await asyncio.to_thread(prices.fetch_quote, transport, app_id, STEAM_CC)
    problem = quote_problem(quote, app_id)
    if problem:
        await ctx.reply_ephemeral(problem, public_fallback=True)
        return
    watch_id, created = await bot.store.run(db.add_watch, ctx.author.id, app_id, quote.name, target_cents, quote.currency, quote.cents)
    now, target_text = prices.format_cents(quote.cents, quote.currency), prices.format_cents(target_cents, quote.currency)
    verb = "watching" if created else "updated the watch on"
    where = "" if await configured_channel() else f" No deals channel is set yet, so ask a moderator to run `{bot.prefix}deals setup`."
    await ctx.reply_ephemeral(f"{verb} **{clean(quote.name)}** as `{watch_id}`: now {now}, I will mention you in the deals channel at {target_text} or less.{where}", public_fallback=True)


@watch_group.command(name="list", help="Your price watches")
async def watch_list(ctx):
    watches = await bot.store.run(db.list_watches, ctx.author.id)
    if not watches:
        await ctx.reply_ephemeral(f"no watches yet - try `{bot.prefix}watch <steam link or app id> <target price>`.", public_fallback=True)
        return
    lines = []
    for w in watches:
        last = prices.format_cents(w["last_price_cents"], w["currency"]) if w["last_price_cents"] is not None else "not checked yet"
        lines.append(f"`{w['id']}` {clean(w['name'])} - now {last}, target {prices.format_cents(w['target_price_cents'], w['currency'])}")
    await ctx.reply_ephemeral("\n".join(lines), public_fallback=True)


@watch_group.command(name="remove", help="Stop a price watch", usage="<id>")
async def watch_remove(ctx, watch_id: int):
    removed = await bot.store.run(db.remove_watch, watch_id, ctx.author.id)
    await ctx.reply_ephemeral(f"removed watch `{watch_id}`." if removed else f"you have no watch `{watch_id}`.", public_fallback=True)


def main():
    try:
        raise SystemExit(bot.run() or 0)
    except RuntimeError as err:
        raise SystemExit(str(err))


if __name__ == "__main__":
    main()
