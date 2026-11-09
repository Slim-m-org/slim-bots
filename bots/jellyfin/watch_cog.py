"""bot-jellyfin's `!pause`/`!resume`/`!seek`/`!np`/`!stop`/`!subs`/`!audio`/`!quality` commands and the `!watch` entry point; see README.md."""

from __future__ import annotations

from slimbots import Permissions
from slimbots.media import format_hms, parse_hms
from slimbots.voice import VoiceError

import answers
import controls
import jellyfin_core
import panel
import picker
import quality
import session_registry
import tracks
import watch_start
from stream_session import StreamError


ENDED = "that watch party has ended."


def _may_control(ctx, session):
    return ctx.author.id == session.started_by_id or ctx.author.has_permission(Permissions.MANAGE_CHANNELS)


async def run_pause(ctx):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    if not _may_control(ctx, session):
        await answers.tell(ctx, "only the person who started this, or a channel manager, can pause it.")
        return
    async with session.control_lock:
        paused = session.pause()
    await answers.tell(ctx, "paused." if paused else "already paused.")
    await answers.tidy(ctx)
    if paused:
        await session.refresh_panel()


async def run_resume(ctx):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    if not _may_control(ctx, session):
        await answers.tell(ctx, "only the person who started this, or a channel manager, can resume it.")
        return
    async with session.control_lock:
        resumed = session.resume()
    await answers.tell(ctx, "resumed." if resumed else "already playing.")
    await answers.tidy(ctx)
    if resumed:
        await session.refresh_panel()


async def run_seek(ctx, position_text):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    if not _may_control(ctx, session):
        await answers.tell(ctx, "only the person who started this, or a channel manager, can seek it.")
        return
    try:
        seconds = parse_hms(position_text or "")
    except ValueError:
        await answers.tell(ctx, "give a time like `1:02:03`, `2:03`, or a bare second count.")
        return
    async with session.control_lock:
        if session.finished:
            await answers.tell(ctx, ENDED)
            return
        await session.seek(seconds)
    await answers.tell(ctx, f"seeked to {format_hms(session.position_seconds)}.")
    await answers.tidy(ctx)
    await session.refresh_panel()


async def run_stop(ctx):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    if not _may_control(ctx, session):
        await answers.tell(ctx, "only the person who started this, or a channel manager, can stop it.")
        return
    async with session.control_lock:
        await session.stop(reason=f"stopped by {ctx.author.display_name}")
    await answers.tidy(ctx)


async def run_now_playing(ctx):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    await answers.tell(ctx, embed=session.now_playing_embed())
    await answers.tidy(ctx)


async def run_subs(ctx, language):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    if not _may_control(ctx, session):
        await answers.tell(ctx, "only the person who started this, or a channel manager, can change subtitles.")
        return
    language = (language or "").strip()
    track = None
    if language.lower() == "next":
        if not jellyfin_core.subtitle_streams(session.item):
            await answers.tell(ctx, "this title has no subtitle tracks.")
            return
        track = tracks.next_subtitle(session.item, session.subtitle_stream_index)
    elif language and language.lower() != tracks.OFF:
        track = jellyfin_core.find_subtitle_stream(session.item, language)
        if track is None:
            await answers.tell(ctx, f'no subtitle track matching "{language}".')
            return
    async with session.control_lock:
        if session.finished:
            await answers.tell(ctx, ENDED)
            return
        await controls.show_subtitle(session, track)
    await answers.tell(ctx, "subtitles off." if track is None else f"subtitles set to {session.subtitle_label}.")
    await answers.tidy(ctx)
    await session.refresh_panel()


def _audio_listing(session):
    heard = tracks.playing_audio(session.item, session.audio_stream_index)
    named = [f"{tracks.label(a)}{' (playing)' if a is heard else ''}" for a in tracks.streams(session.item, "Audio")]
    if not named:
        return "this title lists no audio tracks."
    return f"audio tracks: {', '.join(named)}. change it with `{session.bot.prefix}audio <language|next>`."


async def run_audio(ctx, language):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    language = (language or "").strip()
    if not language:
        await answers.tell(ctx, _audio_listing(session))
        return
    if not _may_control(ctx, session):
        await answers.tell(ctx, "only the person who started this, or a channel manager, can change the audio.")
        return
    if len(tracks.streams(session.item, "Audio")) < 2:
        await answers.tell(ctx, "this title has no other audio track.")
        return
    if language.lower() == "next":
        track = tracks.next_audio(session.item, session.audio_stream_index)
    else:
        track = tracks.find_audio(session.item, language)
    if track is None:
        await answers.tell(ctx, f'no audio track matching "{language}".')
        return
    async with session.control_lock:
        if session.finished:
            await answers.tell(ctx, ENDED)
            return
        await session.set_audio(track["Index"])
    await answers.tell(ctx, f"audio set to {tracks.label(track)}.")
    await answers.tidy(ctx)
    await session.refresh_panel()


async def run_quality(ctx, preset_name):
    session = await session_registry.resolve_session(ctx)
    if session is None:
        return
    preset_name = (preset_name or "").strip()
    if not preset_name:
        await answers.tell(ctx, f"quality is {session.quality.describe()}. change it with `{ctx.bot.prefix}quality <{'|'.join(quality.PRESETS)}>`.")
        return
    preset = quality.find_preset(preset_name)
    if preset is None:
        await answers.tell(ctx, f'no quality called "{preset_name}" - try {quality.preset_names()}.')
        return
    if not _may_control(ctx, session):
        await answers.tell(ctx, "only the person who started this, or a channel manager, can change the quality.")
        return
    try:
        async with session.control_lock:
            if session.finished:
                await answers.tell(ctx, ENDED)
                return
            await session.set_quality(preset)
    except (StreamError, VoiceError) as err:
        await answers.tell(ctx, f"could not switch quality: {err}")
        return
    note = f" {quality.HEAVY_WARNING}" if preset.is_heavy else ""
    await answers.tell(ctx, f"quality set to {preset.describe()}, resumed at {format_hms(session.position_seconds)}.{note}")
    await answers.tidy(ctx)
    await session.refresh_panel()


def setup(bot):
    @bot.command(name="watch", help="Start a watch party in your voice channel; with no title, offers the last thing you were watching", usage="[title]")
    async def watch(ctx, query: str = ""):
        await watch_start.run_watch(ctx, query)

    @bot.command(name="pause", help="Pause the current watch party")
    async def pause(ctx):
        await run_pause(ctx)

    @bot.command(name="resume", help="Resume the current watch party")
    async def resume(ctx):
        await run_resume(ctx)

    @bot.command(name="seek", help="Jump to a position in the current watch party", usage="<h:mm:ss>")
    async def seek(ctx, position: str = ""):
        await run_seek(ctx, position)

    @bot.command(name="stop", help="Stop the current watch party")
    async def stop(ctx):
        await run_stop(ctx)

    @bot.command(name="np", help="Show what's currently playing")
    async def now_playing(ctx):
        await run_now_playing(ctx)

    @bot.command(name="subs", help="Set or turn off burned-in subtitles", usage="<lang|next|off>")
    async def subs(ctx, language: str = ""):
        await run_subs(ctx, language)

    @bot.command(name="audio", help="List the audio tracks, or switch to one", usage="[language|next]")
    async def audio(ctx, language: str = ""):
        await run_audio(ctx, language)

    @bot.command(name="quality", help="Show or change the stream quality", usage="[low|medium|high]")
    async def quality_command(ctx, preset: str = ""):
        await run_quality(ctx, preset)

    bot.button(prefix=panel.ID_PREFIX)(controls.on_panel_press)
    picker.CHOOSER.register(bot)
    for control_id, label, icon, action in controls.CALL_CONTROLS:
        bot.call_control(control_id, label, icon=icon)(controls.call_control_handler(action))
    bot.call_control(
        controls.QUALITY_CONTROL_ID, "Quality", icon="settings", options=controls.quality_options(),
    )(controls.on_quality_control)
    bot.call_control(
        controls.JUMP_CONTROL_ID, "Jump to", icon="list", options=controls.jump_options(),
    )(controls.on_jump_control)
    bot.call_control(
        controls.CAPTIONS_CONTROL_ID, "Captions", icon="captions", options=controls.captions_options(),
    )(controls.on_captions_control)
    bot.call_control(
        controls.AUDIO_CONTROL_ID, "Audio", icon="volume", options=controls.audio_options(),
    )(controls.on_audio_control)

    @bot.event
    async def on_voice_activity(event):
        session = session_registry.session_for_channel(event.channel_id)
        if session is not None:
            session.wake_monitor()
