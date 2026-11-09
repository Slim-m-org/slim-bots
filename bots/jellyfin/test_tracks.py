#!/usr/bin/env python3
"""Language codes and which subtitle or audio stream a title plays with; run directly: python3 test_tracks.py."""

import languages
import tracks
from slimbots.testing import run_module_tests
from test_bot import jellyfin


def sub(index, language, title=None, **flags):
    return {"Type": "Subtitle", "Index": index, "Language": language, "DisplayTitle": title or language, **flags}


def audio(index, language, title=None, **flags):
    return {"Type": "Audio", "Index": index, "Language": language, "DisplayTitle": title or language, **flags}


def item_with(*streams):
    return {"Id": "e1", "Name": "Show", "Type": "Episode", "MediaStreams": list(streams)}


def indexes(plan_or_track):
    track = plan_or_track.track if hasattr(plan_or_track, "track") else plan_or_track
    return None if track is None else track["Index"]


def test_a_code_in_either_639_2_form_a_639_1_code_or_a_name_is_one_language():
    for spellings, key in (
        (("ja", "jpn", "Japanese", "JA"), "jpn"), (("es", "spa", "Spanish"), "spa"), (("pt", "por", "portuguese"), "por"),
        (("sv", "swe", "Swedish"), "swe"), (("fr", "fre", "fra", "French"), "fre"), (("zh", "chi", "zho", "Chinese"), "chi"),
        (("de", "ger", "deu", "german"), "ger"), (("en", "eng", "English", "en-US", "en_GB"), "eng"),
    ):
        assert {languages.language_key(text) for text in spellings} == {key}, spellings


def test_an_unlisted_three_letter_code_stands_for_itself_and_noise_is_no_language():
    assert languages.language_key("xyz") == "xyz"
    assert languages.language_key("jap") == "jpn", "an unambiguous prefix of a name"
    for noise in (None, "", "  ", "und", "mul", "zxx", "x", "undetermined"):
        assert languages.language_key(noise) is None, noise
    assert languages.language_name("jpn") == "Japanese" and languages.language_name("xyz") == "XYZ"


def test_spanish_portuguese_and_swedish_tracks_are_found_by_their_two_letter_codes():
    item = item_with(sub(1, "spa", "Spanish - SRT"), sub(2, "por", "Portuguese (Brazil)"), sub(3, "swe", "Svenska"))
    assert indexes(tracks.find_subtitle(item, "es")) == 1
    assert indexes(tracks.find_subtitle(item, "pt")) == 2
    assert indexes(tracks.find_subtitle(item, "sv")) == 3, "the display title says Svenska, so only the language tag can match"
    assert indexes(tracks.find_subtitle(item, "japanese")) is None


def test_a_track_tagged_with_the_other_639_2_form_still_matches():
    item = item_with(sub(1, "fra", "Français"), sub(2, "ger", "Deutsch"))
    assert indexes(tracks.find_subtitle(item, "french")) == 1
    assert indexes(tracks.find_subtitle(item, "fre")) == 1
    assert indexes(tracks.find_subtitle(item, "deu")) == 2


def test_a_language_is_matched_as_a_language_never_as_a_substring_of_a_title():
    item = item_with(sub(1, "eng", "Genesis cut"), sub(2, "jpn", "Japanese"))
    assert indexes(tracks.find_subtitle(item, "gen")) is None, "gen is not a language and not a whole word of a title"
    assert indexes(tracks.find_subtitle(item, "genesis")) == 1, "a whole word of a title still names it"


def test_the_dialogue_track_beats_signs_forced_and_commentary_in_the_same_language():
    item = item_with(
        sub(1, "eng", "English - Signs & Songs", IsDefault=True), sub(2, "eng", "English - Forced", IsForced=True),
        sub(3, "eng", "English - Commentary"), sub(4, "eng", "English - ASS"),
    )
    assert indexes(tracks.find_subtitle(item, "en")) == 4
    assert indexes(tracks.default_subtitle(item, "eng")) == 4


def test_a_track_without_hearing_cues_is_preferred_and_the_file_default_breaks_a_tie():
    item = item_with(sub(1, "eng", "English SDH", IsDefault=True), sub(2, "eng", "English"), sub(3, "eng", "English (alt)", IsDefault=True))
    assert indexes(tracks.find_subtitle(item, "eng")) == 3
    assert indexes(tracks.find_subtitle(item_with(sub(1, "eng", "English", IsHearingImpaired=True), sub(2, "eng", "English")), "eng")) == 2


def test_only_signs_in_the_language_is_still_what_an_explicit_request_gets():
    item = item_with(sub(1, "eng", "English - Signs & Songs"), sub(2, "spa", "Spanish"))
    assert indexes(tracks.find_subtitle(item, "eng")) == 1
    assert indexes(tracks.default_subtitle(item, "eng")) == 1
    assert indexes(tracks.default_subtitle(item, "fre", "spa")) == 2, "the first wanted language that has a track"
    assert indexes(tracks.default_subtitle(item, None, "ger")) == 2, "no wanted language has one, so the best of any: dialogue first"


def test_captions_come_on_by_themselves_only_when_the_audio_is_not_in_the_preferred_language():
    japanese = item_with(audio(1, "jpn", IsDefault=True), audio(2, "eng"), sub(3, "eng", "English - Signs", IsForced=True), sub(4, "eng"))
    pick = lambda item, **kw: indexes(tracks.pick_subtitle(item, kw.pop("choice", None), preferred=kw.pop("preferred", "eng"), audio_index=kw.pop("audio_index", None)))
    assert pick(japanese) == 4
    assert pick(japanese, audio_index=2) is None, "the English dub is being heard"
    assert pick(japanese, preferred=None) is None, "the setting is off"
    assert pick(item_with(audio(1, "eng"), sub(2, "eng"))) is None, "an English show needs none"
    assert pick(item_with(audio(1, "jpn"), sub(2, "eng", "English - Signs", IsForced=True))) is None, "signs alone are no captions"
    assert pick(item_with(audio(1, "und"), sub(2, "eng"))) is None, "an unknown spoken language is not a reason"
    assert pick(item_with(sub(1, "eng"))) is None, "no audio streams listed"
    assert pick(item_with(audio(1, "jpn"), sub(2, "spa"))) is None, "nothing in the preferred language"


def test_a_chosen_language_or_off_wins_over_the_automatic_rule():
    japanese = item_with(audio(1, "jpn"), sub(2, "eng"), sub(3, "jpn", "Japanese"))
    assert tracks.pick_subtitle(japanese, tracks.OFF, preferred="eng", audio_index=None) is None
    assert indexes(tracks.pick_subtitle(japanese, "jpn", preferred="eng", audio_index=None)) == 3
    assert tracks.pick_subtitle(japanese, "spa", preferred="eng", audio_index=None) is None
    assert indexes(tracks.pick_subtitle(item_with(audio(1, "eng"), sub(2, "spa")), "spa", preferred="eng", audio_index=None)) == 2


def test_an_explicit_pick_becomes_the_standing_choice_for_later_titles():
    item = item_with(audio(1, "jpn"), audio(2, "eng"), sub(3, "eng"), sub(4, "und", "Mystery"))
    assert tracks.caption_choice_for(item, None) == tracks.OFF
    assert tracks.caption_choice_for(item, 3) == "eng"
    assert tracks.caption_choice_for(item, 4) is None, "a track with no language has nothing to carry"
    assert tracks.audio_choice_for(item, 2) == "eng"
    assert tracks.audio_choice_for(item, 9) is None


def test_next_subtitle_steps_through_the_tracks_then_off_then_the_first():
    item = item_with(sub(5, "eng"), sub(3, "jpn"), sub(9, "spa"))
    seen = [indexes(tracks.next_subtitle(item, None))]
    while seen[-1] is not None:
        seen.append(indexes(tracks.next_subtitle(item, seen[-1])))
    assert seen == [3, 5, 9, None], "file order by stream index, and off after the last"
    assert tracks.next_subtitle(item_with(), None) is None


def test_audio_is_picked_by_language_with_commentary_last_and_the_default_a_tie_break():
    item = item_with(audio(1, "eng", "English commentary"), audio(2, "eng"), audio(3, "jpn", IsDefault=True))
    assert tracks.pick_audio(item, "eng") == 2
    assert tracks.pick_audio(item, "jpn") == 3
    assert tracks.pick_audio(item, "spa") is None and tracks.pick_audio(item, None) is None
    assert indexes(tracks.original_audio(item)) == 2, "the first track that is not commentary"
    assert tracks.audio_language(item, None) == "jpn", "what Jellyfin plays when asked for nothing"
    assert tracks.audio_language(item, 2) == "eng"


def test_the_dock_captions_plan_names_a_track_or_says_why_not():
    item = item_with(audio(1, "jpn"), sub(2, "eng"), sub(3, "jpn", "Japanese"))
    plan = lambda option, **kw: tracks.plan_captions(item, option, preferred=kw.get("preferred", "eng"), subtitle_index=kw.get("subtitle_index"), audio_index=None)
    assert plan("off") == tracks.Plan(None, None)
    assert indexes(plan("preferred")) == 2
    assert indexes(plan("audio")) == 3, "subtitles in the language being spoken"
    assert indexes(plan("next")) == 2 and indexes(plan("next", subtitle_index=3)) is None
    assert plan("preferred", preferred="spa").problem == "no Spanish subtitle track on this title."
    assert plan("preferred", preferred=None).problem == "no preferred language subtitle track on this title."
    assert plan("bogus").problem == "that captions choice does not exist."
    bare = tracks.plan_captions(item_with(audio(1, "jpn")), "preferred", preferred="eng", subtitle_index=None, audio_index=None)
    assert bare.problem == "this title has no subtitle tracks."
    assert tracks.plan_captions(item_with(audio(1, "jpn")), "off", preferred="eng", subtitle_index=None, audio_index=None).problem is None
    unknown = tracks.plan_captions(item_with(audio(1, "und"), sub(2, "eng")), "audio", preferred="eng", subtitle_index=None, audio_index=None)
    assert unknown.problem == "the language of the audio is not listed on this title."


def test_the_dock_audio_plan_needs_a_second_track():
    item = item_with(audio(1, "jpn", IsDefault=True), audio(2, "eng"), audio(3, "eng", "English commentary"))
    plan = lambda option, **kw: tracks.plan_audio(item, option, preferred=kw.get("preferred", "eng"), audio_index=kw.get("audio_index"))
    assert indexes(plan("original")) == 1 and indexes(plan("preferred")) == 2
    assert [indexes(plan("next", audio_index=i)) for i in (None, 1, 2, 3)] == [2, 2, 3, 1], "wraps, and starts from the default"
    assert plan("preferred", preferred="spa").problem == "no Spanish audio track on this title."
    assert plan("bogus").problem == "that audio choice does not exist."
    single = tracks.plan_audio(item_with(audio(1, "jpn")), "next", preferred="eng", audio_index=None)
    assert single.problem == "this title has no other audio track."


def test_the_configured_language_is_a_key_or_off_and_nothing_else():
    core = jellyfin.jellyfin_core
    assert core.parse_language("en", "X") == "eng" and core.parse_language(" Japanese ", "X") == "jpn"
    assert core.parse_language("off", "X") is None and core.parse_language("", "X") is None and core.parse_language(None, "X") is None
    try:
        core.parse_language("klingonese", "JELLYFIN_SUBTITLE_LANGUAGE")
    except RuntimeError as err:
        assert "JELLYFIN_SUBTITLE_LANGUAGE" in str(err)
    else:
        raise AssertionError("an unknown language was accepted")


if __name__ == "__main__":
    run_module_tests(globals())
