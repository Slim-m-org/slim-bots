#!/usr/bin/env python3
"""`!quality` command and `WatchSession.set_quality`, on the fakes from test_bot.py; run directly: python3 test_quality.py."""

import os
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import quality  # noqa: E402
import stream_session  # noqa: E402
import session_registry  # noqa: E402
import watch_cog  # noqa: E402
from slimbots.testing import FakeVoiceSession  # noqa: E402
from test_bot import _fake_start_pipeline, jellyfin, message, movie_for_watch, process, setup_with_voice  # noqa: E402


def running_session(voice_session=None):
    session = stream_session.WatchSession(
        jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", voice_session or FakeVoiceSession("c1"),
    )
    session._video_source = session._audio_source = object()
    session_registry.add(session)
    return session


def patch_pipeline():
    original = (stream_session.WatchSession._start_pipeline, stream_session.WatchSession._teardown_pipeline)

    async def no_teardown(self):
        return None

    stream_session.WatchSession._start_pipeline = _fake_start_pipeline
    stream_session.WatchSession._teardown_pipeline = no_teardown
    return original


def restore_pipeline(original):
    stream_session.WatchSession._start_pipeline, stream_session.WatchSession._teardown_pipeline = original
    session_registry.clear()


def test_quality_alone_reports_the_current_setting():
    client = setup_with_voice()
    running_session()
    try:
        process(client, message("!quality"))
        reply = client.sent[-1]["content"]
        assert "quality is default (" in reply and "low|medium|high" in reply
    finally:
        session_registry.clear()


def test_quality_needs_a_running_stream():
    client = setup_with_voice()
    process(client, message("!quality high"))
    assert client.sent[-1]["content"] == "nothing is playing."


def test_quality_rejects_an_unknown_preset():
    client = setup_with_voice()
    running_session()
    try:
        process(client, message("!quality ultra"))
        assert 'no quality called "ultra"' in client.sent[-1]["content"]
    finally:
        session_registry.clear()


def test_quality_refuses_a_non_starter_non_manager():
    client = setup_with_voice()
    session = running_session()
    session.started_by_id = "someone-else"
    try:
        process(client, message("!quality low"))
        assert "only the person who started this" in client.sent[-1]["content"]
        assert session.quality.name == "default"
    finally:
        session_registry.clear()


def test_quality_republishes_at_the_preset_size_and_keeps_the_position():
    client = setup_with_voice()
    voice_session = FakeVoiceSession("c1")
    session = running_session(voice_session)
    session._seek_base = 600.0
    session.paused = True
    original = patch_pipeline()
    try:
        process(client, message("!quality low"))
    finally:
        restore_pipeline(original)
    assert session.quality is quality.PRESETS["low"]
    assert voice_session.publish_count == 1
    assert (voice_session.published["width"], voice_session.published["height"]) == (854, 480)
    assert voice_session.published["video_max_bitrate"] == 1_500_000
    assert session.paused and abs(session.position_seconds - 600) < 1
    assert client.sent[-1]["content"] == "quality set to low (854x480, up to 1500 kbps), resumed at 10:00."


def test_a_1080p_preset_warns_about_the_cpu_cost():
    client = setup_with_voice()
    running_session()
    original = patch_pipeline()
    try:
        process(client, message("!quality high"))
    finally:
        restore_pipeline(original)
    assert quality.HEAVY_WARNING in client.sent[-1]["content"]


def test_the_high_preset_publishes_just_under_the_vp8_eight_thread_cutoff():
    client = setup_with_voice()
    voice_session = FakeVoiceSession("c1")
    running_session(voice_session)
    original = patch_pipeline()
    try:
        process(client, message("!quality high"))
    finally:
        restore_pipeline(original)
    width, height = voice_session.published["width"], voice_session.published["height"]
    assert (width, height) == (1920, 1072)
    assert width * height < quality.VP8_EIGHT_THREAD_AREA


def test_only_a_1080p_class_size_is_trimmed():
    assert quality.below_vp8_thread_jump(1920, 1080) == (1920, 1072)
    assert quality.below_vp8_thread_jump(1920, 1088) == (1920, 1072)
    assert quality.below_vp8_thread_jump(1920, 1072) == (1920, 1072)
    assert quality.below_vp8_thread_jump(1280, 720) == (1280, 720)
    assert quality.below_vp8_thread_jump(2560, 1440) == (2560, 1440)
    assert quality.PRESETS["high"].frame_size == (1920, 1072)
    assert quality.PRESETS["medium"].frame_size == (1280, 720)


def test_a_failed_republish_is_reported_not_raised():
    client = setup_with_voice()
    running_session(FakeVoiceSession("c1", can_publish=False))
    original = patch_pipeline()
    try:
        process(client, message("!quality low"))
    finally:
        restore_pipeline(original)
    assert client.sent[-1]["content"].startswith("could not switch quality:")


def test_the_stream_url_and_np_embed_follow_the_preset():
    url = stream_session.jellyfin_core.build_stream_url("m1", max_width=854, video_bitrate=1_500_000)
    assert "MaxWidth=854" in url and "VideoBitrate=1500000" in url
    session = running_session()
    try:
        session.quality = quality.PRESETS["medium"]
        fields = {f["name"]: f["value"] for f in session.now_playing_embed().to_wire()["fields"]}
        assert fields["quality"] == "medium (1280x720, up to 4000 kbps)"
    finally:
        session_registry.clear()


def _with_rate(rate):
    return {"Id": "m1", "Name": "film", "MediaStreams": [{"Type": "Audio"}, {"Type": "Video", "RealFrameRate": rate}]}


def test_playback_fps_keeps_the_titles_own_rate_or_a_whole_fraction_of_it():
    assert quality.playback_fps(_with_rate(23.976025), 30) == Fraction(24000, 1001)
    assert quality.playback_fps(_with_rate(25.0), 30) == 25
    assert quality.playback_fps(_with_rate(29.97003), 30) == Fraction(30000, 1001)
    assert quality.playback_fps(_with_rate(50.0), 30) == 25
    assert quality.playback_fps(_with_rate(59.94006), 30) == Fraction(30000, 1001)
    assert quality.playback_fps(_with_rate(60.0), 30) == 30
    assert quality.playback_fps({"MediaStreams": []}, 30) == 30
    assert quality.playback_fps(_with_rate(None), 24) == 24


def test_a_film_is_decoded_and_paced_at_its_own_rate():
    session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", _with_rate(23.976025), "u1", FakeVoiceSession("c1"))
    args = stream_session.build_video_args("http://jf/x", "", width=1280, height=720, fps=session.fps)
    assert session.fps == Fraction(24000, 1001)
    assert args[args.index("-vf") + 1].endswith(",fps=24000/1001")


def test_the_video_decode_runs_on_a_fixed_small_thread_count():
    args = stream_session.build_video_args("http://jf/x", "", width=1920, height=1072, fps=24)
    assert args.index("-threads") < args.index("-i"), "a -threads after -i sets the encoder, not the decoder"
    assert args[args.index("-threads") + 1] == str(stream_session.DECODE_THREADS)


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
