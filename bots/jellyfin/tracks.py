"""Which subtitle and audio stream a title plays with, and which one a member's request means; see README.md."""

from __future__ import annotations

import re
from collections import namedtuple

import languages

OFF = "off"
PARTIAL = re.compile(r"\b(forced|signs?|songs?|karaoke|lyrics|commentary)\b")
HEARING = re.compile(r"\b(sdh|cc|hearing)\b")
COMMENTARY = re.compile(r"\bcommentary\b")

Plan = namedtuple("Plan", "track problem")


def streams(item, kind):
    """A title's `Subtitle` or `Audio` streams in file order."""
    found = [s for s in item.get("MediaStreams") or [] if s.get("Type") == kind]
    return sorted(found, key=lambda s: s.get("Index", 0))


def _text(stream):
    return f"{stream.get('Title') or ''} {stream.get('DisplayTitle') or ''}".lower()


def stream_language(stream):
    """The language key a stream is tagged with, or None when it is untagged or `und`."""
    return languages.language_key(stream.get("Language")) or languages.language_key(stream.get("DisplayLanguage"))


def is_partial(stream):
    """Forced, signs-and-songs, karaoke or commentary: a track that is not the dialogue."""
    return bool(stream.get("IsForced")) or bool(PARTIAL.search(_text(stream)))


def is_hearing_impaired(stream):
    return bool(stream.get("IsHearingImpaired")) or bool(HEARING.search(_text(stream)))


def label(stream):
    return stream.get("DisplayTitle") or stream.get("Title") or stream.get("Language") or "on"


def _subtitle_rank(stream):
    return (is_partial(stream), is_hearing_impaired(stream), not stream.get("IsDefault"), stream.get("Index", 0))


def _audio_rank(stream):
    return (bool(COMMENTARY.search(_text(stream))), not stream.get("IsDefault"), stream.get("Index", 0))


def _best(candidates, rank):
    return min(candidates, key=rank) if candidates else None


def subtitle_in(item, key):
    """The full-dialogue subtitle track in a language, before any signs, forced or commentary track."""
    return _best([s for s in streams(item, "Subtitle") if stream_language(s) == key], _subtitle_rank)


def audio_in(item, key):
    return _best([s for s in streams(item, "Audio") if stream_language(s) == key], _audio_rank)


def _titled(candidates, word, rank):
    word = word.strip().lower()
    if not word:
        return None
    pattern = re.compile(rf"\b{re.escape(word)}\b")
    return _best([s for s in candidates if pattern.search(_text(s))], rank)


def find_subtitle(item, language):
    """The subtitle track a typed language names, by code or name; a whole word of a track's title still counts."""
    key = languages.language_key(language)
    return (key and subtitle_in(item, key)) or _titled(streams(item, "Subtitle"), language, _subtitle_rank)


def find_audio(item, language):
    key = languages.language_key(language)
    return (key and audio_in(item, key)) or _titled(streams(item, "Audio"), language, _audio_rank)


def default_subtitle(item, *wanted):
    """The track the Subtitles button turns on: the best in the first wanted language that has one, else the best in any."""
    for key in wanted:
        found = key and subtitle_in(item, key)
        if found:
            return found
    return _best(streams(item, "Subtitle"), _subtitle_rank)


def playing_audio(item, audio_index):
    """The audio stream being heard: the chosen one, else the one Jellyfin plays when asked for nothing."""
    audios = streams(item, "Audio")
    chosen = next((a for a in audios if a.get("Index") == audio_index), None) if audio_index is not None else None
    return chosen or next((a for a in audios if a.get("IsDefault")), audios[0] if audios else None)


def audio_language(item, audio_index):
    playing = playing_audio(item, audio_index)
    return None if playing is None else stream_language(playing)


def original_audio(item):
    """The first audio track that is not commentary: releases put the original language first."""
    audios = streams(item, "Audio")
    return next((a for a in audios if not COMMENTARY.search(_text(a))), audios[0] if audios else None)


def next_subtitle(item, current_index):
    """The track after the one showing, none after the last, and the first when none is showing."""
    subs = streams(item, "Subtitle")
    here = next((i for i, s in enumerate(subs) if s.get("Index") == current_index), None) if current_index is not None else None
    if here is None:
        return subs[0] if subs else None
    return subs[here + 1] if here + 1 < len(subs) else None


def next_audio(item, audio_index):
    audios = streams(item, "Audio")
    playing = playing_audio(item, audio_index)
    return audios[(audios.index(playing) + 1) % len(audios)] if audios and playing is not None else None


def pick_audio(item, choice):
    """The AudioStreamIndex a standing language choice means for this title, or None to leave it to Jellyfin."""
    found = None if choice is None else audio_in(item, choice)
    return None if found is None else found["Index"]


def pick_subtitle(item, choice, *, preferred, audio_index):
    """The track a title starts with: none when captions were turned off, the chosen language's when one was picked,
    else the preferred language's dialogue track when the audio being heard is in another language."""
    if choice == OFF:
        return None
    if choice is not None:
        return subtitle_in(item, choice)
    spoken = audio_language(item, audio_index)
    if preferred is None or spoken is None or spoken == preferred:
        return None
    track = subtitle_in(item, preferred)
    return None if track is None or is_partial(track) else track


def caption_choice_for(item, subtitle_index):
    """What an explicit pick of a subtitle track (or none) becomes as a standing choice for later titles."""
    if subtitle_index is None:
        return OFF
    track = next((s for s in streams(item, "Subtitle") if s.get("Index") == subtitle_index), None)
    return None if track is None else stream_language(track)


def audio_choice_for(item, audio_index):
    track = next((a for a in streams(item, "Audio") if a.get("Index") == audio_index), None)
    return None if track is None else stream_language(track)


def plan_captions(item, option, *, preferred, subtitle_index, audio_index):
    """What a dock Captions choice does here: the track to show (None for none, which `next` reaches after the last), or why not."""
    if option == OFF:
        return Plan(None, None)
    if not streams(item, "Subtitle"):
        return Plan(None, "this title has no subtitle tracks.")
    if option == "next":
        return Plan(next_subtitle(item, subtitle_index), None)
    if option == "preferred":
        wanted = preferred
    elif option == "audio":
        wanted = audio_language(item, audio_index)
        if wanted is None:
            return Plan(None, "the language of the audio is not listed on this title.")
    else:
        return Plan(None, "that captions choice does not exist.")
    track = wanted and subtitle_in(item, wanted)
    if not track:
        return Plan(None, f"no {languages.language_name(wanted) if wanted else 'preferred language'} subtitle track on this title.")
    return Plan(track, None)


def plan_audio(item, option, *, preferred, audio_index):
    """What a dock Audio choice does here: the track to hear, or why it cannot be done."""
    audios = streams(item, "Audio")
    if len(audios) < 2:
        return Plan(None, "this title has no other audio track.")
    if option == "next":
        return Plan(next_audio(item, audio_index), None)
    if option == "original":
        return Plan(original_audio(item), None)
    if option != "preferred":
        return Plan(None, "that audio choice does not exist.")
    track = preferred and audio_in(item, preferred)
    if not track:
        return Plan(None, f"no {languages.language_name(preferred) if preferred else 'preferred language'} audio track on this title.")
    return Plan(track, None)
