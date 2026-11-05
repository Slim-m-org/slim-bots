"""Where a drawing goes: the voice call the invoker is in, and whether the bot may use its canvas there."""

from slimbots import ApiError, Permissions
from slimbots.http import is_forbidden

NEEDED = (("View channels", Permissions.VIEW_CHANNEL), ("Use the canvas", Permissions.USE_CANVAS))


async def find_call(ctx):
    """(channel id, channel name) of the invoker's current call, or a reply sentence."""
    bot = ctx.bot
    channel_id = await bot.voice.find_member(ctx.author.id)
    if channel_id is None:
        return f"join a voice channel first, then run `{bot.prefix}draw` again; I draw on the canvas of the call you are in."
    channel = bot.space.channels.get(channel_id)
    return channel_id, channel.name if channel else "your call"


async def missing_rights(bot, channel_id, name):
    """A sentence naming what the bot lacks for `channel_id`'s canvas, or None when it can use it."""
    try:
        bits = int((await bot.client.member_permissions(bot.me_id or ""))["permissions"])
    except (ApiError, KeyError, ValueError):
        bits = None
    if bits is not None:
        absent = [label for label, bit in NEEDED if not Permissions.contains(bits, bit)]
        if absent:
            return f"I cannot draw in {name}: I am missing {' and '.join(absent)}. An admin can grant them to my role."
    try:
        await bot.canvas(channel_id).viewport(min_x=0, min_y=0, max_x=1, max_y=1, limit=1)
    except ApiError as err:
        if is_forbidden(err):
            return f"I cannot draw in {name}: a setting on that channel stops me from viewing it or using its canvas."
        raise
    return None


async def resolve(ctx):
    """(channel id, channel name) the invoker's drawing goes to, or a reply sentence."""
    found = await find_call(ctx)
    if isinstance(found, str):
        return found
    problem = await missing_rights(ctx.bot, *found)
    return problem if problem else found
