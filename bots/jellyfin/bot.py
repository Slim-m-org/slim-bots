#!/usr/bin/env python3
"""bot-jellyfin: posts new Jellyfin library items, and answers `!jellyfin search|recent|help`; see README.md.
Split per docs/framework.md: this is the entry point and the poll loop, jellyfin_core.py holds the rest."""

import sys

from slimbots import ApiError, Bot, style
from slimbots.http import is_token_revoked

import jellyfin_core
import playback_progress

bot = Bot(
    prefix="!", require_channels=True, listen_voice_chats=True,
    default_data_path="jellyfin_watch.db", store_migrate=jellyfin_core.init_db,
)
jellyfin_core.configure(bot)
playback_progress.configure(bot)

bot.load_extension("search_cog")
bot.load_extension("watch_cog")


async def upload_poster(poster_item_id):
    """Re-hosts a Jellyfin item's primary image as a slim-m attachment, or None if either step fails."""
    if not poster_item_id:
        return None
    image_bytes = await jellyfin_core.jf_get_bytes(f"/Items/{poster_item_id}/Images/Primary")
    if not image_bytes:
        return None
    try:
        attachment = await bot.client.upload_attachment(image_bytes, filename="poster.jpg")
        return attachment.id if attachment else None
    except ApiError as err:
        if is_token_revoked(err):
            raise
        return None


async def send_post(entry):
    """The title rides once: as the embed when a poster attachment carries the post, else as plain text."""
    assert bot.client is not None, "send_post runs only once connected"
    attachment_id = await upload_poster(entry["poster_item_id"])
    channel_id = jellyfin_core.target_channel(bot, entry.get("library_id"))
    text = jellyfin_core.render_text(entry)
    if not attachment_id:
        await bot.client.send(channel_id, text, message_id=entry["message_id"])
        return
    embed = jellyfin_core.render_embed(entry)
    await bot.client.send(
        channel_id, "", message_id=entry["message_id"], attachment_ids=[attachment_id],
        **style.send_kwargs(embed, text),
    )


async def bootstrap():
    """Jellyfin is read off the store lock, so a slow server never stalls a lookup."""
    if await bot.store.run(jellyfin_core.get_cursor) is not None:
        return
    plan = await jellyfin_core.bootstrap_plan()
    await bot.store.run(jellyfin_core.apply_bootstrap, plan)


@bot.every(lambda: jellyfin_core.JELLYFIN_POLL_SECONDS, name="jellyfin-poll", fatal=(jellyfin_core.JellyfinAuthError,), before=bootstrap)
async def poll_once():
    cursor = await bot.store.run(jellyfin_core.get_cursor) or ""
    fetched = await jellyfin_core.items_since(cursor)
    items = await bot.store.run(jellyfin_core.unseen_items, fetched)
    if not items:
        return
    excluded = [item for item in items if jellyfin_core.is_excluded(item)]
    postable = [item for item in items if not jellyfin_core.is_excluded(item)]
    posts = jellyfin_core.build_posts(postable)
    by_id = {item["Id"]: item for item in postable}
    if excluded:
        await bot.store.run(jellyfin_core.mark_posted, [item["Id"] for item in excluded], quiet=True)
        newest = max(item["DateCreated"] for item in excluded)
        await bot.store.run(jellyfin_core.advance_cursor, jellyfin_core.cursor_below_unsent(newest, postable))
    for index, entry in enumerate(posts):
        try:
            await send_post(entry)
        except ApiError as err:
            if is_token_revoked(err):
                raise
            print(f"send failed, will retry next cycle: {err}", file=sys.stderr)
            break
        await bot.store.run(jellyfin_core.mark_posted, entry["item_ids"], items=[by_id[i] for i in entry["item_ids"] if i in by_id])
        still_unsent = [by_id[i] for later in posts[index + 1:] for i in later["item_ids"] if i in by_id]
        await bot.store.run(jellyfin_core.advance_cursor, jellyfin_core.cursor_below_unsent(entry["max_created"], still_unsent))


if __name__ == "__main__":
    bot.run_cli(check=jellyfin_core.check_jellyfin_config)
