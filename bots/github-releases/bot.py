#!/usr/bin/env python3
"""bot-github-releases: posts each new slim-m client and server release with condensed notes; see README.md."""

import asyncio
import sys
import uuid
from dataclasses import dataclass

import feed as feedmod
import notes
from slimbots import ApiError, Bot, KeyValueTable, LedgerTable
from slimbots.http import is_token_revoked

MIN_POLL_SECONDS = 60
POST_NAMESPACE = uuid.UUID("5d0c7a8e-3f61-4c1e-9b7a-2e8d4f6a1c33")


SEEN = LedgerTable("seen", key="release_id", detail="tag")
STATE = KeyValueTable("state")


def init_db(conn):
    SEEN.create(conn)
    STATE.create(conn)
    conn.commit()


def is_initialized(conn):
    return STATE.get(conn, "initialized") is not None


def seen_ids(conn):
    return SEEN.keys(conn)


def mark_seen(conn, releases, initialize=False):
    SEEN.add_many(conn, [(str(r["id"]), r["tag_name"]) for r in releases])
    if initialize:
        STATE.set(conn, "initialized", "1")
    conn.commit()


bot = Bot(prefix="!", require_channels=True, default_data_path="github-releases.db", store_migrate=init_db)


@dataclass
class Config:
    repo: str | None
    token: str | None
    poll_seconds: int
    prefixes: tuple
    announce_latest: bool


def clamp_poll(seconds):
    return max(seconds, MIN_POLL_SECONDS)


config = Config(
    repo=bot.setting("GH_REPO", None),
    token=bot.setting("GITHUB_TOKEN", None),
    poll_seconds=clamp_poll(bot.setting("GH_POLL_SECONDS", 600, type=int)),
    prefixes=tuple(bot.setting("GH_TAG_PREFIXES", ["client-v", "server-v"], type=list)),
    announce_latest=bot.setting("GH_ANNOUNCE_LATEST", False, type=bool),
)


def match_prefix(tag):
    return next((p for p in config.prefixes if tag.startswith(p)), None)


def describe(release, prefix):
    """The product name and version a tag spells, like ('client', '0.93.0') for client-v0.93.0."""
    product = prefix.removesuffix("v").rstrip("-_/ ") or "release"
    return product, release["tag_name"][len(prefix):]


def release_url(release):
    url = release.get("html_url") or ""
    if url.startswith("https://github.com/"):
        return url
    return f"https://github.com/{config.repo}/releases/tag/{release['tag_name']}"


def eligible(release):
    return not release.get("draft") and not release.get("prerelease")


def order_key(release):
    return (release.get("published_at") or "", release["id"] if isinstance(release["id"], int) else 0)


async def post(release, prefix):
    assert bot.client is not None and bot.channel is not None, "post runs only once connected to one channel"
    product, version = describe(release, prefix)
    url = release_url(release)
    await bot.client.send(
        bot.channel, notes.render(product, version, url, release.get("body")),
        message_id=str(uuid.uuid5(POST_NAMESPACE, f"{config.repo}|{release['id']}")),
    )


async def first_run(candidates):
    """Records every release as seen so a new deployment stays quiet; GH_ANNOUNCE_LATEST posts the newest one first."""
    if config.announce_latest:
        matching = [r for r in candidates if match_prefix(r["tag_name"])]
        if matching:
            newest = max(matching, key=order_key)
            await post(newest, match_prefix(newest["tag_name"]))
    await bot.store.run(mark_seen, candidates, True)


async def announce_new(candidates):
    already = await bot.store.run(seen_ids)
    for release in sorted((r for r in candidates if str(r["id"]) not in already), key=order_key):
        prefix = match_prefix(release["tag_name"])
        if prefix:
            await post(release, prefix)
        await bot.store.run(mark_seen, [release])


async def poll_once(feed):
    """One cycle; returns seconds GitHub asked us to wait, or None. A failed post leaves the etag unaccepted."""
    result = await feed.fetch()
    if result.status == "limited":
        print(f"github rate limited ({result.detail}), waiting {result.wait}s", file=sys.stderr)
        return result.wait
    if result.status == "error":
        print(f"github fetch failed: {result.detail}, retrying next cycle", file=sys.stderr)
        return None
    if result.status == "unchanged":
        return None
    candidates = [r for r in result.releases if eligible(r)]
    try:
        if await bot.store.run(is_initialized):
            await announce_new(candidates)
        else:
            await first_run(candidates)
    except ApiError as err:
        if is_token_revoked(err):
            raise
        print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
        return None
    feed.accept(result)
    return None


releases_feed = feedmod.Feed(config.repo, token=config.token)


@bot.every(lambda: config.poll_seconds, name="github-releases-poll")
async def poll():
    return await poll_once(releases_feed)


def require_repo():
    # No default: another community running this should never announce slim-m's own releases by accident.
    if not config.repo:
        return "GH_REPO is not set: name the owner/repo whose releases to announce"
    return None


def main():
    bot.run_cli(check=require_repo)


if __name__ == "__main__":
    main()
