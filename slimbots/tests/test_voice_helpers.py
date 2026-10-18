"""The playback helpers music and jellyfin share: joining to speak, starting or leaving, the microphone track, the empty-call watchdog."""

import asyncio
import sys

import pytest

from slimbots import ApiError, media
from slimbots.bot import Bot
from slimbots.testing import FakeAsyncClient, FakeAudioSource, FakeVoice, FakeVoiceSession
from slimbots.voice import VoiceError, join_to_speak, publish_microphone, start_or_leave, watch_empty


async def test_join_to_speak_returns_the_session():
    voice = FakeVoice()
    session = await join_to_speak(voice, "v1", label="#room", to="play audio")
    assert session is voice.sessions[0] and not session.left


async def test_a_failed_join_names_the_channel_in_the_reply_text():
    voice = FakeVoice(join_error=VoiceError("needs VIEW_CHANNEL and CONNECT in that channel"))
    with pytest.raises(VoiceError) as caught:
        await join_to_speak(voice, "v1", label="#room", to="play audio")
    assert str(caught.value) == "can't join #room: needs VIEW_CHANNEL and CONNECT in that channel"


async def test_a_session_that_cannot_publish_is_left_and_refused():
    voice = FakeVoice(can_publish=False)
    with pytest.raises(VoiceError) as caught:
        await join_to_speak(voice, "v1", label="#room", to="stream video/audio")
    assert str(caught.value) == "I can join #room but can't speak there - I need SPEAK to stream video/audio."
    assert voice.sessions[0].left


async def test_start_or_leave_lets_a_good_start_through():
    session = FakeVoiceSession("v1")

    async def start():
        return None

    await start_or_leave(session, start(), doing="playing")
    assert not session.left


async def test_a_failed_start_leaves_and_raises_a_reply_text(capsys):
    session = FakeVoiceSession("v1")

    async def start():
        raise RuntimeError("engine timed out")

    with pytest.raises(VoiceError) as caught:
        await start_or_leave(session, start(), doing="playing")
    assert str(caught.value) == "could not start playing: engine timed out" and session.left
    assert "RuntimeError starting playing: engine timed out" in capsys.readouterr().err


async def test_an_expected_failure_is_not_logged(capsys):
    session = FakeVoiceSession("v1")

    class PlayerError(Exception):
        pass

    async def start():
        raise PlayerError("no ffmpeg")

    with pytest.raises(VoiceError, match="no ffmpeg"):
        await start_or_leave(session, start(), doing="playing", expected=(PlayerError,))
    assert capsys.readouterr().err == ""


async def test_a_revoked_token_leaves_and_is_reraised_as_it_was():
    session = FakeVoiceSession("v1")

    async def start():
        raise ApiError(401, "revoked")

    with pytest.raises(ApiError):
        await start_or_leave(session, start(), doing="playing")
    assert session.left


class FakeRtc:
    class TrackSource:
        SOURCE_MICROPHONE = "microphone"

    class AudioEncoding:
        def __init__(self, max_bitrate):
            self.max_bitrate = max_bitrate

    class TrackPublishOptions:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class LocalAudioTrack:
        @staticmethod
        def create_audio_track(name, source):
            return {"name": name, "source": source}

    @staticmethod
    def AudioSource(sample_rate, num_channels):
        return FakeAudioSource()


class FakeParticipant:
    def __init__(self):
        self.published = []

    async def publish_track(self, track, options):
        self.published.append((track, options))


async def test_publish_microphone_publishes_a_microphone_track_at_the_bitrate():
    session = FakeVoiceSession("v1")
    session.rtc = FakeRtc()
    participant = FakeParticipant()
    session.room = type("Room", (), {"local_participant": participant})()
    source = await publish_microphone(session, max_bitrate=96_000)
    track, options = participant.published[0]
    assert isinstance(source, FakeAudioSource) and track["name"] == "music"
    assert options.source == "microphone" and options.audio_encoding.max_bitrate == 96_000


async def test_publish_microphone_refuses_a_session_that_cannot_publish():
    with pytest.raises(VoiceError, match="cannot publish"):
        await publish_microphone(FakeVoiceSession("v1", can_publish=False), max_bitrate=1)


def watched_bot(rosters):
    bot = Bot(prefix="!")
    bot.client = FakeAsyncClient()
    bot.me_id = "me"
    queue = list(rosters)
    bot.client.respond("GET", "/channels/v1/voice/roster", lambda: {"participants": queue.pop(0)})
    return bot


async def test_the_watchdog_fires_once_when_only_the_bot_is_left():
    bot = watched_bot([[{"user_id": "me"}, {"user_id": "u1"}], [{"user_id": "me"}]])
    wake, fired = asyncio.Event(), []

    async def on_empty():
        fired.append(1)

    await watch_empty(bot, "v1", wake, on_empty, poll=0.001)
    assert fired == [1]


async def test_the_watchdog_stops_without_a_roster_call_once_done():
    bot = watched_bot([])
    await watch_empty(bot, "v1", asyncio.Event(), lambda: None, poll=0.001, is_done=lambda: True)
    assert bot.client.calls == []


async def test_the_tick_runs_before_each_roster_check():
    bot = watched_bot([[{"user_id": "u1"}], []])
    wake, ticks = asyncio.Event(), []
    wake.set()

    async def on_empty():
        return None

    async def tick():
        ticks.append(1)

    await watch_empty(bot, "v1", wake, on_empty, poll=0.001, on_tick=tick)
    assert len(ticks) == 2


async def test_a_wake_cuts_a_long_poll_short():
    bot = watched_bot([[{"user_id": "me"}]])
    wake, fired = asyncio.Event(), []
    wake.set()

    async def on_empty():
        fired.append(1)

    await asyncio.wait_for(watch_empty(bot, "v1", wake, on_empty, poll=3600), timeout=5)
    assert fired == [1]


def test_hms_formats_and_parses():
    assert media.format_hms(0) == "0:00" and media.format_hms(59.9) == "0:59" and media.format_hms(3723) == "1:02:03"
    assert media.format_hms(-5) == "0:00" and media.format_hms(86399) == "23:59:59"
    assert media.parse_hms("1:02:03") == 3723 and media.parse_hms("2:03") == 123 and media.parse_hms(" 45 ") == 45
    for bad in ["", "a", "1:2:3:4", "1:-2", "1.5", "::"]:
        with pytest.raises(ValueError):
            media.parse_hms(bad)


def test_pcm_chunk_math_is_20ms_of_48k_stereo_16_bit():
    assert media.pcm_chunk_samples() == 960 and media.pcm_chunk_bytes() == 3840
    assert media.pcm_chunk_bytes(10) == 1920


def test_ffmpeg_binary_names_the_error_class_it_is_given(monkeypatch):
    monkeypatch.setattr(media.shutil, "which", lambda name: None)

    class StreamError(media.MediaError):
        pass

    with pytest.raises(StreamError, match="ffmpeg is not on PATH"):
        media.ffmpeg_binary(StreamError)
    monkeypatch.setattr(media.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    assert media.ffmpeg_binary() == "/usr/bin/ffmpeg"


async def test_reap_kills_a_running_process_and_drains_it():
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", "import time; time.sleep(30)", stdout=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(media.reap(process), timeout=10)
    assert process.returncode is not None
    await media.reap(process)
