#!/usr/bin/env python3
"""A party's captions and audio: chosen for a title, carried to the next episode, and driven from the dock and `!subs`/`!audio`.
Run directly: python3 test_captions.py."""

import asyncio

import controls
import jellyfin_core
import session_registry
import stream_session
import tracks
from slimbots.testing import FakeVoiceSession, run_module_tests
from test_bot import jellyfin, message, process
from test_panel import client_in_call, started_session, use_control
from test_resume import Patched
from test_seek import Rig
from test_tracks import audio, item_with, sub


def japanese_episode(item_id="e1", first=1):
    """Japanese audio first with an English dub, an English signs track and full English and Spanish subtitles."""
    return {
        **item_with(
            audio(first, "jpn", IsDefault=True), audio(first + 1, "eng"),
            sub(first + 2, "eng", "English - Signs & Songs", IsForced=True), sub(first + 3, "eng", "English"), sub(first + 4, "spa", "Spanish"),
        ),
        "Id": item_id, "RunTimeTicks": 1_800 * 10_000_000, "SeriesId": "s1",
    }


def english_movie():
    return {**item_with(audio(1, "eng"), sub(2, "eng")), "Id": "m1", "Type": "Movie", "RunTimeTicks": 1_800 * 10_000_000}


def with_rig(item_factory, test):
    def wrapper():
        with Patched((jellyfin_core, "SUBTITLE_LANGUAGE", "eng")):
            rig = Rig(item=item_factory())
            try:
                test(rig)
            finally:
                rig.close()
    wrapper.__name__ = test.__name__
    return wrapper


def title_rig(item_factory):
    return lambda test: with_rig(item_factory, test)


def launched_with(rig, text):
    return text in rig.jf.launches[-1]


@title_rig(japanese_episode)
def test_a_title_in_another_language_starts_with_english_captions_burned_in(rig):
    rig.run(rig.start())
    assert launched_with(rig, "SubtitleStreamIndex=4") and "AudioStreamIndex" not in rig.jf.launches[-1]
    assert rig.session.subtitle_label == "English", "the dialogue track, not the signs track the file flags forced"


@title_rig(english_movie)
def test_a_title_in_the_preferred_language_starts_without_captions(rig):
    rig.run(rig.start())
    assert "SubtitleStreamIndex" not in rig.jf.launches[-1] and rig.session.subtitle_label is None


@title_rig(japanese_episode)
def test_captions_chosen_for_one_episode_follow_the_party_to_the_next(rig):
    async def scenario():
        await rig.start()
        await controls.show_subtitle(rig.session, tracks.find_subtitle(rig.session.item, "spanish"))
        await rig.session.play_item(japanese_episode("e2", first=10))
    rig.run(scenario())
    assert launched_with(rig, "SubtitleStreamIndex=14"), "the Spanish track of the new episode, not the old index"
    assert rig.session.subtitle_label == "Spanish"


@title_rig(japanese_episode)
def test_captions_turned_off_stay_off_for_the_next_episode(rig):
    async def scenario():
        await rig.start()
        await controls.show_subtitle(rig.session, None)
        await rig.session.play_item(japanese_episode("e2", first=10))
    rig.run(scenario())
    assert "SubtitleStreamIndex" not in rig.jf.launches[-1] and rig.session.subtitle_stream_index is None


@title_rig(japanese_episode)
def test_automatic_captions_are_worked_out_again_for_each_episode(rig):
    async def scenario():
        await rig.start()
        await rig.session.play_item(japanese_episode("e2", first=10))
    rig.run(scenario())
    assert launched_with(rig, "SubtitleStreamIndex=13")


@title_rig(japanese_episode)
def test_the_audio_track_chosen_follows_the_party_by_language(rig):
    async def scenario():
        await rig.start()
        await rig.session.set_audio(2)
        await rig.session.play_item(japanese_episode("e2", first=10))
    rig.run(scenario())
    assert launched_with(rig, "AudioStreamIndex=11")


@title_rig(japanese_episode)
def test_hearing_the_english_dub_drops_the_captions_the_bot_chose_and_the_original_brings_them_back(rig):
    async def scenario():
        await rig.start()
        await rig.session.set_audio(2)
        dubbed = rig.jf.launches[-1], rig.session.subtitle_stream_index
        await rig.session.set_audio(1)
        return dubbed
    dubbed_url, dubbed_subtitle = rig.run(scenario())
    assert "AudioStreamIndex=2" in dubbed_url and "SubtitleStreamIndex" not in dubbed_url and dubbed_subtitle is None
    assert launched_with(rig, "AudioStreamIndex=1") and launched_with(rig, "SubtitleStreamIndex=4")


@title_rig(japanese_episode)
def test_captions_the_member_chose_are_kept_when_the_audio_changes(rig):
    async def scenario():
        await rig.start()
        await controls.show_subtitle(rig.session, tracks.find_subtitle(rig.session.item, "spanish"))
        await rig.session.set_audio(2)
    rig.run(scenario())
    assert launched_with(rig, "AudioStreamIndex=2") and launched_with(rig, "SubtitleStreamIndex=5")


@title_rig(japanese_episode)
def test_choosing_what_is_already_showing_does_not_restart_the_transcode(rig):
    async def scenario():
        await rig.start()
        before = len(rig.jf.launches)
        await controls.show_subtitle(rig.session, tracks.find_subtitle(rig.session.item, "english"))
        await rig.session.set_audio(1)
        return before
    before = rig.run(scenario())
    assert len(rig.jf.launches) == before


class Reading:
    """Sets the language members read captions in for a with-block, leaving the live party registry alone."""

    def __init__(self, key):
        self.key = key

    def __enter__(self):
        self.saved, jellyfin_core.SUBTITLE_LANGUAGE = jellyfin_core.SUBTITLE_LANGUAGE, self.key

    def __exit__(self, *_exc):
        jellyfin_core.SUBTITLE_LANGUAGE = self.saved


def captioned_session(client, item=None):
    with Reading("eng"):
        return started_session(client, item or japanese_episode())


def test_the_call_dock_offers_captions_and_audio_beside_the_other_controls():
    asyncio.run(jellyfin.bot.update_call_control("jf-playpause", icon="pause", label="Pause"))
    wire = {e.to_wire()["id"]: e.to_wire() for e, _ in jellyfin.bot._ui.controls.values()}
    assert (wire["jf-captions"]["icon"], wire["jf-audio"]["icon"]) == ("captions", "volume")
    assert [o["id"] for o in wire["jf-captions"]["options"]] == ["off", "preferred", "audio", "next"]
    assert [o["label"] for o in wire["jf-captions"]["options"]] == ["Off", "English", "Audio language", "Next track"]
    assert [o["id"] for o in wire["jf-audio"]["options"]] == ["original", "preferred", "next"]
    assert len(wire) <= 8, "the server shows at most eight controls from one bot"


def test_without_a_preferred_language_the_dock_names_none():
    with Patched((jellyfin_core, "SUBTITLE_LANGUAGE", None)):
        assert [o[0] for o in controls.captions_options()] == ["off", "audio", "next"]
        assert [o[0] for o in controls.audio_options()] == ["original", "next"]


def test_the_dock_captions_choice_turns_captions_on_in_a_language_and_off():
    client = client_in_call()
    session = captioned_session(client)
    use_control(client, session, "jf-captions", option_id="off")
    assert session.subtitle_stream_index is None and session.caption_choice == tracks.OFF
    use_control(client, session, "jf-captions", option_id="preferred")
    assert session.subtitle_stream_index == 4 and session.caption_choice == "eng"
    use_control(client, session, "jf-captions", option_id="audio")
    assert client.ephemerals[-1]["content"] == "no Japanese subtitle track on this title."
    assert session.subtitle_stream_index == 4, "a refused choice leaves the captions as they were"
    session_registry.clear()


def test_the_dock_captions_choice_can_follow_the_language_being_spoken():
    client = client_in_call()
    item = item_with(audio(1, "jpn"), sub(2, "eng"), sub(3, "jpn", "Japanese"))
    session = captioned_session(client, item)
    assert session.subtitle_stream_index == 2
    use_control(client, session, "jf-captions", option_id="audio")
    assert session.subtitle_stream_index == 3 and session.subtitle_label == "Japanese"
    session_registry.clear()


def test_the_dock_captions_next_track_steps_through_the_file_then_off_then_round_again():
    client = client_in_call()
    session = captioned_session(client)
    seen = []
    for _ in range(4):
        use_control(client, session, "jf-captions", option_id="next")
        seen.append(session.subtitle_stream_index)
    assert seen == [5, None, 3, 4], "from the automatic English dialogue track: Spanish, off, the signs track, English again"
    session_registry.clear()


def test_a_dock_choice_that_cannot_work_here_says_why_privately_and_changes_nothing():
    client = client_in_call()
    session = captioned_session(client, english_movie())
    use_control(client, session, "jf-audio", option_id="next")
    assert client.ephemerals[-1]["content"] == "this title has no other audio track."
    with Reading("spa"):
        use_control(client, session, "jf-captions", option_id="preferred")
    assert client.ephemerals[-1]["content"] == "no Spanish subtitle track on this title."
    use_control(client, session, "jf-captions")
    assert client.ephemerals[-1]["content"] == "pick a captions choice from the list."
    use_control(client, session, "jf-audio")
    assert client.ephemerals[-1]["content"] == "pick an audio track from the list."
    assert session.calls == [] and session.subtitle_stream_index is None
    session_registry.clear()


def test_a_title_with_no_subtitle_tracks_is_told_so_on_the_captions_choice():
    client = client_in_call()
    session = captioned_session(client, item_with(audio(1, "jpn")))
    use_control(client, session, "jf-captions", option_id="preferred")
    assert client.ephemerals[-1]["content"] == "this title has no subtitle tracks."
    session_registry.clear()


def test_the_dock_audio_choice_switches_track_and_a_member_outside_the_call_cannot():
    client = client_in_call()
    session = captioned_session(client)
    use_control(client, session, "jf-audio", option_id="preferred", user_id="u9")
    assert session.audio_stream_index is None and "join #voice-room" in client.ephemerals[-1]["content"]
    use_control(client, session, "jf-audio", option_id="preferred")
    assert session.audio_stream_index == 2 and session.audio_choice == "eng"
    assert session.subtitle_stream_index is None, "the English dub needs no English captions"
    use_control(client, session, "jf-audio", option_id="original")
    assert session.audio_stream_index == 1 and session.subtitle_stream_index == 4
    session_registry.clear()


def test_typed_subs_takes_a_language_in_any_spelling_and_next():
    client = client_in_call()
    session = captioned_session(client)
    for typed, expected in (("!subs es", 5), ("!subs eng", 4), ("!subs next", 5), ("!subs off", None)):
        process(client, message(typed))
        assert session.subtitle_stream_index == expected, typed
    process(client, message("!subs klingon"))
    assert client.ephemerals[-1]["content"] == 'no subtitle track matching "klingon".'
    session_registry.clear()


def test_typed_audio_lists_the_tracks_then_switches_by_language_or_next():
    client = client_in_call()
    session = captioned_session(client)
    process(client, message("!audio"))
    assert client.ephemerals[-1]["content"] == "audio tracks: jpn (playing), eng. change it with `!audio <language|next>`."
    process(client, message("!audio english"))
    assert session.audio_stream_index == 2 and client.ephemerals[-1]["content"] == "audio set to eng."
    process(client, message("!audio next"))
    assert session.audio_stream_index == 1
    process(client, message("!audio klingon"))
    assert client.ephemerals[-1]["content"] == 'no audio track matching "klingon".'
    session_registry.clear()


def test_typed_audio_on_a_title_with_one_track_says_so():
    client = client_in_call()
    session = captioned_session(client, english_movie())
    process(client, message("!audio english"))
    assert client.ephemerals[-1]["content"] == "this title has no other audio track."
    assert session.audio_stream_index is None
    session_registry.clear()


if __name__ == "__main__":
    run_module_tests(globals())
