"""Where a drawing goes: the voice call the invoker is in, and whether the bot may use its canvas there."""

from slimbots import ApiError, Permissions

CANVAS_OFF = "The canvas is off in this channel; someone who manages channels can turn it on in its settings."
NEEDED = (("View channels", Permissions.VIEW_CHANNEL), ("Use the canvas", Permissions.USE_CANVAS))


async def find_call(ctx):
    """(channel id, channel name) of the invoker's current call, or a reply sentence."""
    bot = ctx.bot
    channel_id = await bot.voice.find_member(ctx.author.id)
    if channel_id is None:
        return f"join a voice channel first, then run `{bot.prefix}draw` again; I draw on the canvas of the call you are in."
    channel = bot.space.channels.get(channel_id)
    if channel is not None and not channel.has_canvas:
        return CANVAS_OFF
    return channel_id, channel.name if channel else "your call"


def is_canvas_off(err):
    """Whether `err` is the server's refusal of a closed board, which a toggle can race past the channel check."""
    return isinstance(err, ApiError) and err.status == 403 and isinstance(err.body, dict) and err.body.get("code") == "canvas_off"


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
    here = int((await bot.client.channel_permissions(channel_id))["permissions"])
    if not all(Permissions.contains(here, bit) for _, bit in NEEDED):
        return f"I cannot draw in {name}: a setting on that channel stops me from viewing it or using its canvas."
    return None


async def resolve(ctx):
    """(channel id, channel name) the invoker's drawing goes to, or a reply sentence."""
    found = await find_call(ctx)
    if isinstance(found, str):
        return found
    if not await ctx.author_can(Permissions.USE_CANVAS, found[0]):
        return f"you cannot draw on the canvas in {found[1]}, so I will not draw there for you."
    problem = await missing_rights(ctx.bot, *found)
    return problem if problem else found
