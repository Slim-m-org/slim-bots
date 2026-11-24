"""The live watch parties, one per voice channel the bot is streaming in, and how a command finds the right one."""

from __future__ import annotations

import answers
from slimbots import Permissions

_sessions = {}


def add(session):
    _sessions[session.voice_channel_id] = session


def clear():
    _sessions.clear()


def live_sessions():
    """Channel id -> session for every party still running; a finished one is dropped on the way past."""
    for channel_id in [cid for cid, session in _sessions.items() if session.finished]:
        del _sessions[channel_id]
    return dict(_sessions)


def session_for_channel(channel_id):
    return live_sessions().get(channel_id)


def session_for_panel(message_id):
    """The live party whose now-playing panel is this message, or None once it ended."""
    for session in live_sessions().values():
        if session.panel is not None and session.panel.message_id == message_id:
            return session
    return None


def _names(bot, channel_ids):
    return ", ".join(bot.space.channel_label(channel_id) for channel_id in channel_ids)


def _invoker_can_view(ctx, channel_id):
    """A stopgap until slimbots can check a member's view in one channel: public, the channel typed in, or an administrator."""
    if channel_id == ctx.channel_id or ctx.author.has_permission(Permissions.ADMINISTRATOR):
        return True
    channel = ctx.bot.space.channels.get(channel_id)
    return channel is not None and channel.restricted is False


async def resolve_session(ctx):
    """The party in the voice channel the invoker is in; replies and returns None when there isn't one.
    A person in no call gets the only party they can see, so `!np` still works from a phone; a party they cannot see is never named."""
    live = live_sessions()
    if not live:
        await answers.tell(ctx, "nothing is playing.")
        return None
    channel_id = await ctx.bot.voice.find_member(ctx.author.id)
    if channel_id in live:
        return live[channel_id]
    visible = [cid for cid in live if _invoker_can_view(ctx, cid)]
    if channel_id is None and len(visible) == 1:
        return live[visible[0]]
    running = _names(ctx.bot, visible)
    if channel_id is None:
        await answers.tell(ctx, f"streams are playing in {running} - join the voice channel you mean, then try again." if visible else "nothing is playing.")
    else:
        here = ctx.bot.space.channel_label(channel_id)
        await answers.tell(ctx, f"nothing is playing in {here} (streaming in {running})." if visible else f"nothing is playing in {here}.")
    return None
