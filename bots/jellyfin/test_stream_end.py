#!/usr/bin/env python3
"""What the pumps do when the real ffmpeg pipeline dies or ends; a stand-in script plays ffmpeg. Run directly: python3 test_stream_end.py."""

import asyncio
import os
import stat
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jellyfin_core  # noqa: E402
import playback_progress  # noqa: E402
import quality  # noqa: E402
import session_registry  # noqa: E402
import stream_session  # noqa: E402
from slimbots.testing import FakeVoiceSession  # noqa: E402
from test_bot import jellyfin, movie_for_watch, setup_with_voice  # noqa: E402

WIDTH = HEIGHT = 16
FRAME_BYTES = stream_session.frame_byte_size(WIDTH, HEIGHT)
FRAMES = 5

STAND_IN = """#!/bin/sh
audio=0
case " $* " in *" -vn "*) audio=1;; esac
case "$STAND_IN_MODE" in
dead) exit 1;;
noaudio) [ $audio = 1 ] && exit 1; exec sleep 30;;
*)
  [ $audio = 1 ] && exec sleep 30
  for out; do :; done
  if [ "$out" = pipe:1 ]; then head -c $((FRAME_BYTES * FRAMES)) /dev/zero; else head -c $((FRAME_BYTES * FRAMES)) /dev/zero > "$out"; fi
  [ "$STAND_IN_MODE" = clean ] && exit 0
  exit 1;;
esac
"""


class FakeRtc:
    class VideoBufferType:
        I420 = 1

    class VideoFrame:
        def __init__(self, *args):
            self.args = args

    class AudioFrame:
        def __init__(self, *args):
            self.args = args


class Rig:
    """A real WatchSession whose ffmpeg is a shell stand-in in `mode`; `reports` collects what Jellyfin would be told."""

    def __init__(self, mode, runtime_seconds=7200):
        setup_with_voice()
        self.client = jellyfin.bot.client
        self.reports = []
        script = os.path.join(tempfile.mkdtemp(prefix="slimm-stand-in-"), "ffmpeg")
        with open(script, "w") as handle:
            handle.write(STAND_IN)
        os.chmod(script, os.stat(script).st_mode | stat.S_IXUSR)
        os.environ.update(STAND_IN_MODE=mode, FRAME_BYTES=str(FRAME_BYTES), FRAMES=str(FRAMES))
        self.saved = (stream_session.ffmpeg_binary, playback_progress.report_position, playback_progress.resolve_user_id, jellyfin_core.next_episode)
        stream_session.ffmpeg_binary = lambda: script
        playback_progress.report_position = lambda item_id, seconds, *, finished=False, user_id=None: self.reports.append((round(seconds, 1), finished))
        playback_progress.resolve_user_id = lambda: "u1"
        jellyfin_core.next_episode = lambda *a, **k: None
        self.voice = FakeVoiceSession("c1")
        self.voice.rtc = FakeRtc
        self.session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(runtime_seconds=runtime_seconds), "u1", self.voice)
        self.session.quality = quality.Quality("tiny", WIDTH, HEIGHT, 100_000)
        session_registry.add(self.session)

    def close(self):
        stream_session.ffmpeg_binary, playback_progress.report_position, playback_progress.resolve_user_id, jellyfin_core.next_episode = self.saved
        session_registry.clear()

    async def play_until_finished(self, timeout=5.0):
        await self.session._publish()
        await self.session._start_pipeline(0.0)
        deadline = time.monotonic() + timeout
        while not self.session.finished and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        await asyncio.sleep(0.1)

    def said(self):
        return [sent["content"] for sent in self.client.sent]


def run_rig(mode, runtime_seconds=7200):
    rig = Rig(mode, runtime_seconds)
    try:
        asyncio.run(rig.play_until_finished())
    finally:
        rig.close()
    return rig


def test_an_ffmpeg_that_exits_before_producing_anything_ends_the_session():
    rig = run_rig("dead")
    assert rig.session.finished, "session still up after ffmpeg exited"
    assert rig.voice.left
    assert not any(finished for _, finished in rig.reports), rig.reports
    assert not any("finished playing" in text for text in rig.said()), rig.said()


def test_failed_starts_do_not_pin_the_default_executor():
    rigs = [Rig("dead"), Rig("dead")]

    async def scenario():
        asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=2))
        for rig in rigs:
            await rig.play_until_finished(timeout=2.0)
        return await asyncio.wait_for(asyncio.to_thread(lambda: "ran"), timeout=2.0)

    try:
        assert asyncio.run(scenario()) == "ran"
    finally:
        rigs[0].close()


def test_a_transcode_that_dies_midstream_is_not_reported_finished_or_watched():
    rig = run_rig("midstream")
    assert rig.session.finished, "session still up after ffmpeg dropped"
    assert not any(finished for _, finished in rig.reports), f"a dropped transcode was reported finished: {rig.reports}"
    assert not any("finished playing" in text for text in rig.said()), rig.said()
    assert any("dropped" in text for text in rig.said()), rig.said()


def test_a_clean_exit_far_from_the_end_is_a_dropped_stream_not_the_end():
    rig = run_rig("clean", runtime_seconds=7200)
    assert not any(finished for _, finished in rig.reports), rig.reports
    assert any("dropped" in text for text in rig.said()), rig.said()


def test_a_clean_exit_at_the_end_of_the_title_still_finishes_it():
    rig = run_rig("clean", runtime_seconds=3)
    assert any(finished for _, finished in rig.reports), rig.reports
    assert any("finished playing" in text for text in rig.said()), rig.said()


def test_a_title_with_no_audio_stream_keeps_playing_its_video():
    rig = Rig("noaudio")

    async def scenario():
        await rig.session._publish()
        await rig.session._start_pipeline(0.0)
        await asyncio.sleep(0.5)
        alive = not rig.session.finished
        await rig.session._teardown_pipeline()
        return alive

    try:
        assert asyncio.run(scenario())
    finally:
        rig.close()


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
