"""Private answers to a typed command, so `!pause` and friends leave nothing for the room to scroll past (backlog 163)."""

from __future__ import annotations

from slimbots import ApiError


async def tell(ctx, content="", *, embed=None):
    """Answers only the invoker; the room sees a playback change on the panel and in the call itself.
    Falls back to a public reply once the server refuses a private one (past its 15-minute window), so an error is never lost."""
    try:
        await ctx.reply_ephemeral(content, embed=embed, public_fallback=True)
    except ApiError:
        await ctx.reply(content, embed=embed)


async def tidy(ctx):
    """Deletes the invoker's command once it worked; a bot without MANAGE_MESSAGES there just leaves it."""
    message_id = ctx.message.get("id")
    if message_id is None:
        return
    try:
        await ctx.bot.client.delete_message(ctx.channel_id, message_id)
    except ApiError:
        pass
