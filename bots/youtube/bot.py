#!/usr/bin/env python3
"""bot-youtube: a synced YouTube watch party in the invoker's voice call, with a queue; see README.md.
Every viewer's app plays the video in YouTube's own embed and follows the room's clock, which this bot keeps."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from slimbots import ApiError, Bot, Permissions
from slimbots.http import is_token_revoked
from slimbots.media import format_hms, parse_hms
from slimbots.registration import RegistrationRejected
from slimbots.text import clip, escape_mentions, one_line
from slimbots.voice import VoiceError
from slimbots.web import ServiceError

import party as parties
import youtube_core

bot = Bot(prefix="!", require_channels=True, listen_voice_chats=True)
log = logging.getLogger("youtube")

API_KEY = bot.setting("YOUTUBE_API_KEY", "")
lookup = youtube_core.Lookup(user_agent="slimbots-youtube/1.0", api_key=API_KEY)

LINK_HINT = "give a YouTube link, like `{p}yt https://youtu.be/dQw4w9WgXcQ`."
NOT_PLAYING = "nothing is playing."
PLAYPAUSE_ID = "yt-playpause"
_start_locks: dict[str, asyncio.Lock] = {}


async def tell(ctx, text):
    """Answers only the invoker, falling back to a public reply once the server refuses a private one."""
    try:
        await ctx.reply_ephemeral(text, public_fallback=True)
    except ApiError:
        await ctx.reply(text)


def may_control(user_id, member, party):
    if user_id == party.started_by_id:
        return True
    if member is not None and member.has_permission(Permissions.MANAGE_CHANNELS):
        return True
    current = party.current
    return current is not None and current.queued_by_id == user_id


def plain(text, limit):
    """Outside text made safe to quote: one line, no mentions, no markdown emphasis."""
    return escape_mentions(clip(one_line(text), limit)).replace("*", "\\*").replace("_", "\\_")


def describe(video):
    by = f" from {plain(video.channel, 80)}" if video.channel else ""
    length = f" ({format_hms(video.duration_seconds)})" if video.duration_seconds else ""
    return f"**{plain(video.title, 150)}**{by}{length}"


async def announce(party, video):
    """Posts the link in the call's own chat, so an app that cannot follow the party can still open it."""
    text = f"now watching {describe(video)}: {video.url}"
    try:
        await bot.client.send(party.voice_channel_id, text)
    except ApiError as err:
        if is_token_revoked(err):
            raise
        log.warning("could not post the link in %s: %s", party.voice_channel_id, err)


def dock_look(paused_states):
    """Play when every party is paused, Pause when none is, and a neutral label when calls disagree."""
    if paused_states and all(paused_states):
        return "play", "Play"
    return ("pause", "Play/Pause") if any(paused_states) else ("pause", "Pause")


async def refresh_dock():
    """Best effort: the dock's toggle shows what pressing it will do; a failed update never stops playback."""
    icon, label = dock_look([p.paused for p in parties.live_parties().values()])
    with contextlib.suppress(ApiError, RegistrationRejected):
        await bot.update_call_control(PLAYPAUSE_ID, icon=icon, label=label)


async def resolve(ctx):
    """The party in the invoker's call; with no call, the only one running. Replies and returns None otherwise."""
    live = parties.live_parties()
    if not live:
        await tell(ctx, NOT_PLAYING)
        return None
    channel_id = await bot.voice.find_member(ctx.author.id)
    if channel_id in live:
        return live[channel_id]
    if channel_id is None and len(live) == 1:
        return next(iter(live.values()))
    if channel_id is None:
        await tell(ctx, "join the voice channel whose party you mean, then try again.")
    else:
        await tell(ctx, f"nothing is playing in {bot.space.channel_label(channel_id)}.")
    return None


async def controlled(ctx, verb):
    party = await resolve(ctx)
    if party is None:
        return None
    if not may_control(ctx.author.id, ctx.author, party):
        await tell(ctx, f"only the person who started this party, whoever queued this video, or a channel manager can {verb} it.")
        return None
    return party


async def fetch(ctx, link, queued_by):
    try:
        video = await lookup.video(link.video_id)
    except youtube_core.EmbedRefused:
        await tell(ctx, "that video's owner does not allow it to play outside YouTube, so the room cannot watch it together.")
        return None
    except youtube_core.NoSuchVideo:
        await tell(ctx, "that video is private, removed, or does not exist.")
        return None
    except ServiceError:
        video = youtube_core.Video(link.video_id, "YouTube video")
    return youtube_core.Video(
        video.video_id, video.title, video.channel, video.duration_seconds, queued_by.id, queued_by.display_name,
    )


async def on_party_end(party, reason):
    await refresh_dock()
    if reason not in ("stopped", "disconnected from the call"):
        try:
            await bot.client.send(party.voice_channel_id, f"the watch party ended: {reason}.")
        except ApiError:
            pass


async def start_or_queue(ctx, text):
    link = youtube_core.parse_link(text)
    if link is None:
        await tell(ctx, LINK_HINT.format(p=bot.prefix))
        return
    voice_channel_id = await bot.voice.find_member(ctx.author.id)
    if voice_channel_id is None:
        await tell(ctx, f"join a voice channel first, then run `{bot.prefix}yt <link>` again.")
        return
    lock = _start_locks.setdefault(voice_channel_id, asyncio.Lock())
    async with lock:
        await _start_or_queue_in(ctx, link, voice_channel_id)


async def _start_or_queue_in(ctx, link, voice_channel_id):
    """One at a time per call, so two members starting together get one party and a queue."""
    name = bot.space.channel_label(voice_channel_id)
    party = parties.party_in(voice_channel_id)
    video = await fetch(ctx, link, ctx.author)
    if video is None:
        return
    if party is not None:
        async with party.lock:
            place = party.enqueue(video)
        if place is None:
            await tell(ctx, f"the queue is full ({parties.MAX_QUEUE} videos).")
        else:
            await tell(ctx, f"queued {describe(video)} in {name}, number {place} in line.")
        return
    try:
        session = await bot.voice.join(voice_channel_id)
    except VoiceError as err:
        await tell(ctx, f"I can't join {name}: {err}")
        return
    party = parties.Party(bot, voice_channel_id, session, ctx.author.id, on_end=on_party_end, on_next=announce)
    session.on_disconnect(lambda: party.stop(reason="disconnected from the call"))
    party.start(video, link.start_seconds)
    await tell(ctx, f"watching {describe(video)} in {name}. everyone on the call watches together.")
    await announce(party, video)


@bot.group(name="yt", help="Watch YouTube together in your call: `yt <link>`, `pause`, `resume`, `seek`, `skip`, `queue`, `np`, `stop`",
           suggest={"rest": ("pause", "resume", "seek", "skip", "queue", "np", "stop", "help")})
async def yt(ctx, rest: str = ""):
    if not rest.strip() or rest.strip().lower() == "help":
        await tell(ctx, help_text())
        return
    await start_or_queue(ctx, rest)


def help_text():
    p = bot.prefix
    return (
        f"`{p}yt <link>` starts a watch party in your voice call, or queues the video when one is running. "
        f"`{p}yt pause`, `{p}yt resume`, `{p}yt seek 1:23`, `{p}yt skip`, `{p}yt stop` steer it (its starter, whoever queued the video, or a channel manager). "
        f"`{p}yt queue` and `{p}yt np` show what is on. Everyone's app plays the video in YouTube's own player."
    )


@yt.command(name="pause", help="Pause the room")
async def pause_cmd(ctx):
    party = await controlled(ctx, "pause")
    if party is not None:
        async with party.lock:
            changed = party.pause()
        await refresh_dock()
        await tell(ctx, "paused." if changed else "already paused.")


@yt.command(name="resume", aliases=("play",), help="Resume the room")
async def resume_cmd(ctx):
    party = await controlled(ctx, "resume")
    if party is not None:
        async with party.lock:
            changed = party.resume()
        await refresh_dock()
        await tell(ctx, "resumed." if changed else "already playing.")


@yt.command(name="seek", help="Jump to a time", usage="<h:mm:ss>")
async def seek_cmd(ctx, position: str = ""):
    try:
        seconds = parse_hms(position or "")
    except ValueError:
        await tell(ctx, "give a time like `1:02:03`, `2:03`, or a bare second count.")
        return
    party = await controlled(ctx, "seek")
    if party is not None:
        async with party.lock:
            party.seek(seconds)
        await tell(ctx, f"seeked to {format_hms(party.position_seconds)}.")


@yt.command(name="skip", aliases=("next",), help="Play the next video in the queue")
async def skip_cmd(ctx):
    party = await controlled(ctx, "skip")
    if party is None:
        return
    async with party.lock:
        playing = await party.skip()
    if playing is None:
        await tell(ctx, "that was the last video, so the party is over.")
        return
    await tell(ctx, f"now watching {describe(playing)}.")
    await announce(party, playing)


@yt.command(name="stop", help="End the watch party")
async def stop_cmd(ctx):
    party = await controlled(ctx, "stop")
    if party is not None:
        async with party.lock:
            await party.stop(reason="stopped")
        await tell(ctx, "stopped the watch party.")


def queue_text(party):
    lines = [f"now: {describe(party.current)} at {format_hms(party.position_seconds)}{' (paused)' if party.paused else ''}"]
    for place, video in enumerate(party.queue[:10], start=1):
        lines.append(f"{place}. {describe(video)}, queued by {plain(video.queued_by_name or 'someone', 60)}")
    if len(party.queue) > 10:
        lines.append(f"and {len(party.queue) - 10} more.")
    if not party.queue:
        lines.append(f"nothing queued. `{bot.prefix}yt <link>` adds one.")
    return "\n".join(lines)


@yt.command(name="queue", help="Show what plays next")
async def queue_cmd(ctx):
    party = await resolve(ctx)
    if party is not None:
        await tell(ctx, queue_text(party))


@yt.command(name="np", help="Show what is playing")
async def np_cmd(ctx):
    party = await resolve(ctx)
    if party is not None:
        state = "paused" if party.paused else "playing"
        await tell(ctx, f"{state} {describe(party.current)} at {format_hms(party.position_seconds)}: {party.current.url}")


async def _party_for_press(interaction):
    party = parties.party_in(interaction.channel_id) or parties.party_in(await bot.voice.find_member(interaction.user_id))
    if party is None:
        await interaction.reply_ephemeral("nothing is playing in your call.")
        return None
    member = await bot.space.find_member(interaction.user_id) if bot.space is not None else None
    if not may_control(interaction.user_id, member, party):
        await interaction.reply_ephemeral("only the person who started this party, whoever queued this video, or a channel manager can steer it.")
        return None
    await interaction.ack()
    return party


@bot.call_control(PLAYPAUSE_ID, "Pause", icon="pause")
async def on_playpause(interaction):
    party = await _party_for_press(interaction)
    if party is not None:
        async with party.lock:
            if party.paused:
                party.resume()
            else:
                party.pause()
        await refresh_dock()


@bot.call_control("yt-skip", "Next video", icon="skip_next")
async def on_skip(interaction):
    party = await _party_for_press(interaction)
    if party is None:
        return
    async with party.lock:
        playing = await party.skip()
    if playing is not None:
        await announce(party, playing)


@bot.call_control("yt-stop", "Stop", icon="stop")
async def on_stop(interaction):
    party = await _party_for_press(interaction)
    if party is not None:
        async with party.lock:
            await party.stop(reason="stopped")


@bot.event
async def on_voice_activity(event):
    party = parties.party_in(event.channel_id)
    if party is not None:
        party.wake_monitor()


if __name__ == "__main__":
    bot.run_cli()
