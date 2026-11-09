"""Playback controls shared by the now-playing panel's buttons and the call dock; see README.md."""

from __future__ import annotations

import contextlib

from slimbots import ApiError
from slimbots.voice import VoiceError

import jellyfin_core
import languages
import playback_progress
import quality
import session_registry
import tracks
from panel import ID_PREFIX, SKIP_SECONDS
from stream_session import StreamError


def preflight(session, action):
    """A reason the action cannot run right now, else None; checked before the press is acknowledged."""
    if session.waiting_next is not None and action not in ("next", "stop"):
        return "that episode has finished - press Next episode, or Stop."
    if action == "subs" and session.subtitle_stream_index is None and not jellyfin_core.subtitle_streams(session.item):
        return "this title has no subtitle tracks."
    plan = _plan(session, action)
    if plan is not None and plan.problem:
        return plan.problem
    if action.startswith("q:") and quality.find_preset(action[2:]) is None:
        return "that quality does not exist."
    if action.startswith("jump:") and jump_fraction(action) is None:
        return "that position does not exist."
    if action.startswith("jump:") and not session.duration_seconds:
        return "this title has no known length to jump within."
    return None


JUMP_PERCENTS = (0, 10, 25, 40, 50, 60, 75, 90)
JUMP_CONTROL_ID = "jf-jump"


def jump_fraction(action):
    """The share of the runtime a `jump:<percent>` action names, or None when it is not one of the offered stops."""
    try:
        percent = int(action[len("jump:"):])
    except ValueError:
        return None
    return percent / 100 if percent in JUMP_PERCENTS else None


def jump_options():
    return [(str(percent), "Start" if percent == 0 else f"{percent}% in") for percent in JUMP_PERCENTS]


SKIP_STEPS = {"back": -SKIP_SECONDS, "fwd": SKIP_SECONDS}


def _plan(session, action):
    """The dock's captions or audio choice resolved against this title, or None for any other action."""
    kind, _, option = action.partition(":")
    preferred = jellyfin_core.SUBTITLE_LANGUAGE
    if kind == "cc":
        return tracks.plan_captions(
            session.item, option, preferred=preferred, subtitle_index=session.subtitle_stream_index, audio_index=session.audio_stream_index,
        )
    if kind == "au":
        return tracks.plan_audio(session.item, option, preferred=preferred, audio_index=session.audio_stream_index)
    return None


def _default_subtitle(session):
    """The Subtitles button's track: the language already chosen for this party, else the preferred one, else the best there is."""
    chosen = session.caption_choice if session.caption_choice != tracks.OFF else None
    return tracks.default_subtitle(session.item, chosen, jellyfin_core.SUBTITLE_LANGUAGE)


async def show_subtitle(session, track):
    """Shows `track`, or no subtitles for None."""
    await session.set_subtitle(None if track is None else track["Index"], None if track is None else tracks.label(track))


async def _apply_track_choice(session, action):
    plan = _plan(session, action)
    if plan.problem:
        return plan.problem
    if action.startswith("cc:"):
        await show_subtitle(session, plan.track)
    else:
        await session.set_audio(plan.track["Index"])
    return None


async def _next_episode(session):
    user_id = session.jellyfin_user_id or await playback_progress.resolve_user_id()
    return await jellyfin_core.next_episode(session.item, user_id)


async def _play_next(session):
    upcoming = session.waiting_next or await _next_episode(session)
    if upcoming is None:
        return "that was the last episode."
    await session.play_item(upcoming)
    return None


async def apply(session, action, who="a member"):
    """Runs one control against a party; a sentence for the member when it cannot, else None."""
    if action == "toggle":
        if session.paused:
            session.resume()
        else:
            session.pause()
    elif action in ("back", "fwd"):
        await session.flush_skips()
    elif action.startswith("jump:"):
        await session.seek(session.duration_seconds * jump_fraction(action))
    elif action == "stop":
        await session.stop(reason=f"stopped by {who}")
    elif action.startswith("q:"):
        await session.set_quality(quality.PRESETS[action[2:]])
    elif action == "subs":
        await show_subtitle(session, None if session.subtitle_stream_index is not None else _default_subtitle(session))
    elif action.startswith(("cc:", "au:")):
        return await _apply_track_choice(session, action)
    elif action == "next":
        return await _play_next(session)
    return None


async def _refuse(interaction, text):
    with contextlib.suppress(ApiError):
        await interaction.reply_ephemeral(text)


async def run_control(interaction, session, action):
    """Gate on being in the call, answer the press, then act and redraw the panel."""
    bot = interaction.bot
    voice_channel_id = await bot.voice.find_member(interaction.user_id)
    if voice_channel_id != session.voice_channel_id:
        name = bot.space.channel_label(session.voice_channel_id)
        await _refuse(interaction, f"join {name} to use these controls.")
        return
    problem = preflight(session, action)
    if problem:
        await _refuse(interaction, problem)
        return
    await interaction.ack()
    if action in SKIP_STEPS:
        session.request_skip(SKIP_STEPS[action])
    async with session.control_lock:
        if session.finished:
            return
        try:
            problem = await apply(session, action, interaction.user_display_name or "a member")
        except (StreamError, VoiceError) as err:
            problem = f"that did not work: {err}"
        if problem:
            await bot.client.send(session.text_channel_id, problem)
    if not session.finished:
        await session.refresh_panel()


async def on_panel_press(interaction):
    session = session_registry.session_for_panel(interaction.message_id)
    if session is None:
        await _refuse(interaction, f"that watch party has ended - `{interaction.bot.prefix}watch` starts another.")
        return
    await run_control(interaction, session, interaction.custom_id[len(ID_PREFIX):])


CALL_CONTROLS = (
    ("jf-playpause", "Pause", "pause", "toggle"),
    ("jf-back", f"Back {SKIP_SECONDS}s", "skip_previous", "back"),
    ("jf-forward", f"Forward {SKIP_SECONDS}s", "skip_next", "fwd"),
    ("jf-stop", "Stop", "stop", "stop"),
)


QUALITY_CONTROL_ID = "jf-quality"
CAPTIONS_CONTROL_ID = "jf-captions"
AUDIO_CONTROL_ID = "jf-audio"


def captions_options():
    """The dock's Captions choice: the options cannot follow a title, so each one names a rule the title is checked against."""
    preferred = jellyfin_core.SUBTITLE_LANGUAGE
    named = [("preferred", languages.language_name(preferred))] if preferred else []
    return [(tracks.OFF, "Off"), *named, ("audio", "Audio language"), ("next", "Next track")]


def audio_options():
    preferred = jellyfin_core.SUBTITLE_LANGUAGE
    named = [("preferred", languages.language_name(preferred))] if preferred else []
    return [("original", "Original language"), *named, ("next", "Next track")]


def quality_options():
    """The dock's Quality choice, one per preset, labelled the way the panel's quality row reads."""
    return [(name, f"{name.capitalize()} {preset.height}p") for name, preset in quality.PRESETS.items()]


async def _session_for(interaction):
    session = session_registry.session_for_channel(interaction.channel_id)
    if session is None:
        voice_channel_id = await interaction.bot.voice.find_member(interaction.user_id)
        session = session_registry.session_for_channel(voice_channel_id)
    if session is None:
        await _refuse(interaction, "nothing is playing in your call.")
    return session


def call_control_handler(action):
    """A dock control acts on the party in the call it was used in, through the same path as a panel button."""

    async def handler(interaction):
        session = await _session_for(interaction)
        if session is not None:
            await run_control(interaction, session, action)

    return handler


def option_control_handler(prefix, noun):
    """A dock control that offers options; the pick is the part after `prefix:` of the action, through the panel's own path."""

    async def handler(interaction):
        if interaction.option_id is None:
            await _refuse(interaction, f"pick {noun} from the list.")
            return
        session = await _session_for(interaction)
        if session is not None:
            await run_control(interaction, session, f"{prefix}:{interaction.option_id}")

    return handler


on_jump_control = option_control_handler("jump", "a position")
on_quality_control = option_control_handler("q", "a quality")
on_captions_control = option_control_handler("cc", "a captions choice")
on_audio_control = option_control_handler("au", "an audio track")
