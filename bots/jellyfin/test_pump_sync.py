#!/usr/bin/env python3
"""The `!watch` pumps against a fake ffmpeg on a virtual clock: picture and sound stay together through stalls, underruns and pauses.
Run directly: python3 test_pump_sync.py."""

import asyncio
from types import SimpleNamespace

import pump_sync
import quality
import stream_session
from pump_rig import PacedVoiceSession, VirtualTimeLoop
from slimbots import media
from slimbots.testing import run_module_tests
from test_bot import jellyfin, movie_for_watch, setup_with_voice

FPS = 30
INTERVAL = 1 / FPS
CHUNK_SECONDS = media.PCM_CHUNK_MS / 1000
WIDTH = HEIGHT = 16
FRAME_BYTES = stream_session.frame_byte_size(WIDTH, HEIGHT)
ONE_FRAME = INTERVAL + 0.002
CATCH_UP_FRAMES = round(stream_session.AUDIO_QUEUE_MS / 1000 * FPS) + 2
LEAD = 0.3
AUDIO_QUEUE_SECONDS = stream_session.AUDIO_QUEUE_MS / 1000


def schedule(seconds, period, *, stalled=(), blocked=(), lead=LEAD):
    """When ffmpeg produces each of `seconds` of items, `lead` ahead of real time: a `stalled` (from, to) holds what falls in it until `to`,
    and a `blocked` one holds everything after the item already being read at `from`, as a paused pump leaves ffmpeg waiting on its pipe."""
    times = [max(number * period - lead, 0.0) for number in range(round(seconds / period))]
    for start, end in stalled:
        times = [end if start <= when < end else when for when in times]
    for start, end in blocked:
        in_flight = min((when for when in times if when >= start), default=None)
        times = [when + (end - start) if in_flight is not None and when > in_flight else when for when in times]
    return times


async def feed(reader, times, payload):
    loop = asyncio.get_running_loop()
    origin = loop.time()
    for number, when in enumerate(times):
        await asyncio.sleep(max(origin + when - loop.time(), 0))
        reader.feed_data(payload(number))


async def feed_when(reader, times, payload, ready):
    """Like `feed`, but each item also waits for `ready(number)`: ffmpeg cannot write the sound before it has got the picture out of the way."""
    loop = asyncio.get_running_loop()
    origin = loop.time()
    for number, when in enumerate(times):
        await asyncio.sleep(max(origin + when - loop.time(), 0))
        while not ready(number):
            await asyncio.sleep(0.005)
        reader.feed_data(payload(number))


def numbered_frame(number):
    return number.to_bytes(4, "big") + bytes(FRAME_BYTES - 4)


def silence(_number):
    return bytes(media.pcm_chunk_bytes())


class Playback:
    def __init__(self, rig):
        self.start = rig.start
        self.position = rig.position
        self.heard_at_end = rig.session._audio_source.heard_at(rig.end)
        self.frames = [(when - rig.start, number) for when, number in rig.session._video_source.sent]
        self.sound = rig.session._audio_source

    def offsets(self, after=0.0):
        """Seconds the picture is ahead of the sound, for each frame sent at or after `after`; negative is behind."""
        return [number * INTERVAL - self.sound.heard_at(self.start + when) for when, number in self.frames if when >= after]

    def worst_offset(self, after=0.0, *, skipping=()):
        """The largest offset among frames sent at or after `after`, leaving out frames sent inside any (from, to) of `skipping`."""
        offsets = [
            abs(number * INTERVAL - self.sound.heard_at(self.start + when)) for when, number in self.frames
            if when >= after and not any(start <= when < end for start, end in skipping)
        ]
        return max(offsets, default=None)

    def most_at_once(self):
        times = [when for when, _ in self.frames]
        return max((sum(1 for other in times if 0 <= other - when < 0.005) for when in times), default=0)

    def longest_gap(self):
        times = [when for when, _ in self.frames]
        return max((later - earlier for earlier, later in zip(times, times[1:])), default=0.0)

    def sent_between(self, start, end):
        return [number for when, number in self.frames if start <= when < end]


class Rig:
    """A real WatchSession whose two pumps read fake ffmpeg pipes on a virtual clock."""

    def __init__(self, *, sound=True):
        setup_with_voice()
        self.loop = VirtualTimeLoop()
        self.voice = PacedVoiceSession("c1", self.loop.time)
        self.session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", self.voice)
        self.session.quality = quality.Quality("tiny", WIDTH, HEIGHT, 100_000)
        self.session.fps = FPS
        self.session._launch_pipeline = self._launch
        self.has_sound = sound
        self.start = self.end = self.position = None
        self.video_pipe = self.sound_pipe = None

    async def _launch(self, _start_seconds):
        self.video_pipe, self.sound_pipe = asyncio.StreamReader(), asyncio.StreamReader()
        return SimpleNamespace(stdout=self.video_pipe), SimpleNamespace(stdout=self.sound_pipe) if self.has_sound else None, None

    def play(self, seconds, *, video, sound=None, pauses=(), sound_ends=None, sound_after_frames=None):
        """Plays `seconds` of the title with ffmpeg producing frames and sound chunks at the `video` and `sound` times (seconds after the start);
        `pauses` are (pause, resume) times, `sound_ends` closes the sound pipe then, and `sound_after_frames(n)` is how many frames must be out before chunk n can be written."""
        saved, stream_session.time = stream_session.time, SimpleNamespace(monotonic=self.loop.time)
        try:
            return self.loop.run_until_complete(self._play(seconds, video, sound, pauses, sound_ends, sound_after_frames))
        finally:
            stream_session.time = saved
            self.loop.close()

    async def _play(self, seconds, video, sound, pauses, sound_ends, sound_after_frames):
        session = self.session
        await session._publish()
        await session._start_pipeline(0.0)
        self.start = self.loop.time()
        feeders = [asyncio.create_task(feed(self.video_pipe, video, numbered_frame))]
        if self.has_sound and sound_after_frames is not None:
            sent = session._video_source.sent
            feeders.append(asyncio.create_task(feed_when(self.sound_pipe, sound, silence, lambda number: len(sent) >= sound_after_frames(number))))
        elif self.has_sound:
            feeders.append(asyncio.create_task(feed(self.sound_pipe, sound, silence)))
        if sound_ends is not None:
            self.loop.call_at(self.start + sound_ends, self.sound_pipe.feed_eof)
        for pause, resume in pauses:
            self.loop.call_at(self.start + pause, session.pause)
            self.loop.call_at(self.start + resume, session.resume)
        await asyncio.sleep(seconds)
        self.end, self.position = self.loop.time(), session.position_seconds
        tasks = (*feeders, session._video_task, session._audio_task)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return Playback(self)


def play_title(seconds=10, *, video_stalls=(), sound_stalls=(), blocks=(), **options):
    rig = Rig(sound=options.pop("sound", True))
    video = schedule(seconds, INTERVAL, stalled=video_stalls, blocked=blocks)
    sound = schedule(seconds, CHUNK_SECONDS, stalled=sound_stalls, blocked=blocks)
    return rig.play(seconds, video=video, sound=sound, pauses=options.pop("pauses", ()), **options)


def test_picture_and_sound_stay_within_a_frame_of_each_other_all_the_way():
    playback = play_title(10)
    assert playback.worst_offset() <= ONE_FRAME, playback.worst_offset()
    assert len(playback.frames) >= 10 * FPS - 3, len(playback.frames)


def test_a_stall_in_the_transcode_does_not_leave_the_picture_ahead_of_the_sound():
    playback = play_title(10, video_stalls=[(3.0, 3.6)], sound_stalls=[(3.0, 3.6)])
    assert playback.worst_offset() <= ONE_FRAME, playback.offsets(3.0)[:30]
    assert playback.most_at_once() <= CATCH_UP_FRAMES, f"{playback.most_at_once()} frames went out together after the stall"


def test_sound_that_underruns_holds_the_picture_instead_of_leaving_it_ahead():
    playback = play_title(10, sound_stalls=[(3.0, 3.6)])
    assert playback.worst_offset() <= ONE_FRAME, playback.worst_offset()
    assert playback.sent_between(3.4, 3.6) == [], "the picture ran on through a gap in the sound"


def test_a_transcode_slower_than_real_time_still_shows_every_frame_as_it_arrives():
    rig = Rig()
    slow = [number * INTERVAL * 1.5 for number in range(round(10 / INTERVAL / 1.5))]
    playback = rig.play(10, video=slow, sound=schedule(10, CHUNK_SECONDS))
    assert len(playback.frames) == len(slow), (len(playback.frames), len(slow))
    assert playback.most_at_once() <= 1


def test_a_stall_in_the_picture_alone_catches_up_to_the_sound_without_losing_a_frame():
    playback = play_title(10, video_stalls=[(3.0, 3.4)])
    assert playback.worst_offset(4.0) <= ONE_FRAME, playback.offsets(3.4)[:30]
    assert [number for _, number in playback.frames] == list(range(len(playback.frames)))


def test_pause_and_resume_leave_picture_and_sound_together():
    playback = play_title(10, pauses=[(3.05, 5.07)], blocks=[(3.05, 5.07)])
    assert playback.worst_offset(skipping=[(5.07, 5.3)]) <= ONE_FRAME, playback.offsets(5.0)[:20]
    assert playback.worst_offset() <= AUDIO_QUEUE_SECONDS + ONE_FRAME
    assert playback.most_at_once() <= CATCH_UP_FRAMES
    assert playback.sent_between(3.2, 5.07) == [], "the picture moved while paused"


def test_pauses_at_different_moments_each_leave_picture_and_sound_together():
    pauses = [(1.013, 1.4), (2.0, 2.57), (4.021, 5.3), (6.046, 6.12)]
    playback = play_title(10, pauses=pauses, blocks=pauses)
    settling = [(resume, resume + 0.3) for _, resume in pauses]
    assert playback.worst_offset(skipping=settling) <= ONE_FRAME, playback.worst_offset(skipping=settling)
    assert playback.worst_offset() <= AUDIO_QUEUE_SECONDS + ONE_FRAME


def test_a_title_with_no_sound_keeps_to_the_wall_clock_and_a_pause_does_not_make_it_catch_up():
    rig = Rig(sound=False)
    playback = rig.play(10, video=schedule(10, INTERVAL, blocked=[(3.05, 5.07)]), pauses=[(3.05, 5.07)])
    after = playback.sent_between(5.07, 6.07)
    assert len(after) >= FPS - 4 and playback.most_at_once() <= 2, (len(after), playback.most_at_once())
    assert after == sorted(after) and after[1] - after[0] == 1, "frames were skipped after the pause"


def test_the_reported_position_follows_the_picture_not_the_wall_clock():
    playback = play_title(10, video_stalls=[(3.0, 3.6)], sound_stalls=[(3.0, 3.6)])
    assert abs(playback.position - playback.heard_at_end) <= 2 * INTERVAL, (playback.position, playback.heard_at_end)


def test_the_reported_position_is_not_counted_twice_across_a_pause():
    playback = play_title(10, pauses=[(3.05, 5.07)], blocks=[(3.05, 5.07)])
    assert abs(playback.position - playback.heard_at_end) <= 2 * INTERVAL, (playback.position, playback.heard_at_end)


def test_sound_that_can_only_come_once_the_picture_has_moved_does_not_freeze_both():
    def frames_needed(number):
        return round((number * CHUNK_SECONDS + 0.8) * FPS)

    rig = Rig()
    playback = rig.play(10, video=schedule(10, INTERVAL), sound=schedule(10, CHUNK_SECONDS), sound_after_frames=frames_needed)
    assert len(playback.sent_between(4.0, 10.0)) >= 6 * FPS - 4, len(playback.sent_between(4.0, 10.0))
    assert playback.longest_gap() <= 2 * INTERVAL, f"the picture froze for {playback.longest_gap():.2f}s"


def test_the_picture_carries_on_when_the_sound_stops_arriving():
    rig = Rig()
    playback = rig.play(10, video=schedule(10, INTERVAL), sound=schedule(2, CHUNK_SECONDS))
    assert len(playback.sent_between(6.0, 9.0)) >= 3 * FPS - 4, len(playback.sent_between(6.0, 9.0))


def test_the_picture_carries_on_when_the_sound_ends_first():
    rig = Rig()
    playback = rig.play(10, video=schedule(10, INTERVAL), sound=schedule(2, CHUNK_SECONDS), sound_ends=2.0)
    assert len(playback.sent_between(3.0, 9.0)) >= 6 * FPS - 4, len(playback.sent_between(3.0, 9.0))
    assert playback.longest_gap() <= 2 * INTERVAL, f"the picture froze for {playback.longest_gap():.2f}s when the sound ended"


def test_a_title_without_sound_plays_every_frame_in_time():
    rig = Rig(sound=False)
    playback = rig.play(10, video=schedule(10, INTERVAL))
    assert len(playback.frames) >= 10 * FPS - 3
    assert playback.most_at_once() <= 1


class SoundSource:
    queued_duration = 0.0


class Wall:
    def __init__(self):
        self.now = 50.0


def clock_with_sound():
    wall, source = Wall(), SoundSource()
    clock = pump_sync.MediaClock(source, now=lambda: wall.now)
    clock.begin()
    return clock, wall, source


def test_the_clock_reads_the_sound_that_has_played_out_not_the_sound_pushed():
    clock, wall, source = clock_with_sound()
    clock.push(0.1)
    source.queued_duration = 0.1
    assert clock.position() == 0.0
    wall.now += 0.04
    source.queued_duration = 0.06
    assert abs(clock.position() - 0.04) < 1e-9


def test_the_clock_stays_on_the_wall_for_sound_that_starts_long_after_the_picture():
    clock, wall, source = clock_with_sound()
    wall.now += 3.0
    assert clock.position() == 3.0
    clock.push(0.05)
    source.queued_duration = 0.05
    wall.now += 0.5
    assert abs(clock.position() - 3.5) < 1e-9


def test_giving_up_on_the_sound_carries_on_from_where_it_stopped_and_does_not_follow_it_again():
    clock, wall, source = clock_with_sound()
    clock.push(0.5)
    assert clock.position() == 0.5
    wall.now += 0.1
    clock.give_up()
    wall.now += 0.25
    assert abs(clock.position() - 0.75) < 1e-9
    clock.push(5.0)
    assert abs(clock.position() - 0.75) < 1e-9


def test_a_clock_with_no_sound_runs_on_the_wall_and_resume_skips_the_time_paused():
    wall = Wall()
    clock = pump_sync.MediaClock(None, now=lambda: wall.now)
    clock.begin()
    wall.now += 2.0
    assert clock.position() == 2.0
    wall.now += 30.0
    clock.resume()
    wall.now += 0.5
    assert abs(clock.position() - 2.5) < 1e-9


def test_when_the_sound_ends_the_clock_carries_on_from_where_it_had_got_to():
    clock, wall, source = clock_with_sound()
    clock.push(1.0)
    source.queued_duration = 0.4
    assert abs(clock.position() - 0.6) < 1e-9
    clock.sound_ended()
    wall.now += 1.0
    assert abs(clock.position() - 1.6) < 1e-9


def test_a_frame_the_sound_has_passed_goes_at_once_and_a_paused_one_does_not():
    clock, wall, source = clock_with_sound()
    clock.push(1.0)

    async def verdicts():
        passed = await pump_sync.wait_for_frame(clock, 0.5, stop=lambda: False)
        paused = await pump_sync.wait_for_frame(clock, 0.5, stop=lambda: True)
        return passed, paused

    assert asyncio.run(verdicts()) == (True, False)


if __name__ == "__main__":
    run_module_tests(globals())
