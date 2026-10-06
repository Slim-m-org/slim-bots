"""Jellyfin server-side transcode -> ffmpeg decode -> LiveKit screen-share publish pipeline for `!watch`; see README.md."""

from __future__ import annotations

import asyncio
import contextlib
import shutil
import sys
import time
import uuid

from slimbots import Embed

import jellyfin_core
import playback_progress
from pump_sync import StartLine, reap
from quality import Quality, configured_default, playback_fps
from watch_sync import WatchSync

AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2
AUDIO_CHUNK_MS = 20
AUDIO_QUEUE_MS = 100  # what still plays after a seek or pause; livekit's 1000 ms default left audio a second off the picture
ROSTER_POLL_SECONDS = 20
END_TOLERANCE_SECONDS = 15  # how far short of the runtime a clean ffmpeg exit may land and still count as the title ending
EXIT_WAIT_SECONDS = 5
DECODE_THREADS = 2  # ffmpeg's one-thread-per-core default held 291 MB at 1080p for the same CPU as two threads at 173 MB


class StreamError(Exception):
    """Raised when the ffmpeg pipeline cannot be started - a missing binary, most often."""


def ffmpeg_binary():
    path = shutil.which("ffmpeg")
    if path is None:
        raise StreamError("ffmpeg is not on PATH - install it on the bot's host")
    return path


def build_video_args(url, headers, *, width, height, fps):
    """Letterboxes Jellyfin's own aspect-preserving transcode into an exact WxH - the size `VideoSource` publishes."""
    filters = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}"
    return [
        ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-filter_threads", "1", "-threads", str(DECODE_THREADS),
        "-headers", headers, "-i", url, "-map", "0:v:0", "-an", "-vf", filters, "-pix_fmt", "yuv420p", "-f", "rawvideo", "pipe:1",
    ]


def build_audio_args(url, headers):
    return [
        ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-headers", headers, "-i", url,
        "-map", "0:a:0", "-vn", "-ac", str(AUDIO_CHANNELS), "-ar", str(AUDIO_SAMPLE_RATE),
        "-f", "s16le", "pipe:1",
    ]


def frame_byte_size(width, height):
    """I420: a full-resolution Y plane plus two quarter-resolution chroma planes."""
    return width * height + 2 * ((width + 1) // 2) * ((height + 1) // 2)


def audio_chunk_samples():
    return AUDIO_SAMPLE_RATE * AUDIO_CHUNK_MS // 1000


def audio_chunk_bytes():
    return audio_chunk_samples() * AUDIO_CHANNELS * 2


def format_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"


def parse_hms(text):
    """Parses `h:mm:ss`, `mm:ss`, or a bare second count; raises ValueError on anything else."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise ValueError(f"not a valid time: {text!r}")
    numbers = [int(p) for p in parts]
    while len(numbers) < 3:
        numbers.insert(0, 0)
    hours, minutes, seconds = numbers
    return hours * 3600 + minutes * 60 + seconds


class WatchSession:
    """One `!watch` playback: owns the ffmpeg pipeline, the LiveKit publish, and its own position/pause state."""

    def __init__(self, bot, text_channel_id, voice_channel_id, item, started_by_id, voice_session):
        self.bot = bot
        self.text_channel_id = text_channel_id
        self.voice_channel_id = voice_channel_id
        self._set_item(item)
        self.started_by_id = started_by_id
        self.voice_session = voice_session
        self.panel = None
        self.sync = None
        self.waiting_next = None
        self.jellyfin_user_id = None
        self._next_timer = None
        self.control_lock = asyncio.Lock()
        self.quality = configured_default()
        self.paused = False
        self.finished = False
        self.ended_naturally = False
        self._last_reported_at = time.monotonic()
        self._seek_base = 0.0
        self._segment_started_at = time.monotonic()
        self._video_source = None
        self._audio_source = None
        self._video_process = None
        self._audio_process = None
        self._video_task = None
        self._audio_task = None
        self._monitor_task = None
        self._start_line = StartLine()
        self._wake_monitor = asyncio.Event()

    def _set_item(self, item):
        self.item = item
        self.item_id = item["Id"]
        self.title = item.get("Name") or "Unknown title"
        self.duration_seconds = (item.get("RunTimeTicks") or 0) / 10_000_000
        self.fps = playback_fps(item, jellyfin_core.JELLYFIN_STREAM_FPS)
        self.audio_stream_index = None
        self.subtitle_stream_index = None
        self.subtitle_label = None

    @property
    def position_seconds(self):
        if self.paused or self.finished:
            return self._seek_base
        return self._seek_base + (time.monotonic() - self._segment_started_at)

    def wake_monitor(self):
        """Called from an `on_voice_activity` handler to check the roster now instead of on the next poll tick."""
        self._wake_monitor.set()

    async def _publish(self):
        width, height = self.quality.frame_size
        self._video_source, self._audio_source = await self.voice_session.publish_screen_share(
            width=width, height=height,
            sample_rate=AUDIO_SAMPLE_RATE, num_channels=AUDIO_CHANNELS,
            video_max_bitrate=self.quality.publish_bitrate,
            video_max_framerate=float(jellyfin_core.JELLYFIN_STREAM_FPS),
            audio_max_bitrate=jellyfin_core.JELLYFIN_STREAM_AUDIO_MAX_BITRATE, audio_queue_ms=AUDIO_QUEUE_MS,
        )

    async def start(self, start_seconds=0.0):
        await self._publish()
        await self._start_pipeline(start_seconds)
        self._monitor_task = self.bot.background(self._monitor_loop(), name=f"jellyfin-watch-monitor-{self.voice_channel_id}")
        self.sync = WatchSync(self)
        self.sync.start()

    def _sync_changed(self, *, seeked=False):
        if self.sync is not None:
            self.sync.changed(seeked=seeked)

    async def _launch_pipeline(self, start_seconds):
        """Starts both ffmpeg processes on a fresh transcode without touching the running pipeline; raises if it cannot."""
        processes = []
        try:
            url = jellyfin_core.build_stream_url(
                self.item_id, start_seconds=start_seconds, audio_stream_index=self.audio_stream_index,
                subtitle_stream_index=self.subtitle_stream_index, max_width=self.quality.width,
                video_bitrate=self.quality.video_bitrate, play_session_id=uuid.uuid4().hex,
            )
            headers = f"Authorization: {jellyfin_core.jellyfin_auth_header()}\r\n"
            width, height = self.quality.frame_size
            video_args = build_video_args(url, headers, width=width, height=height, fps=self.fps)
            audio_args = build_audio_args(url, headers)
            for args in (video_args, audio_args):
                processes.append(await asyncio.create_subprocess_exec(
                    *args, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                ))
        except BaseException:
            for process in processes:
                process.kill()
            raise
        return processes

    async def _start_pipeline(self, start_seconds):
        """Replaces the running pipeline only once the new one has launched, so a failed launch leaves playback alone."""
        processes = await self._launch_pipeline(start_seconds)
        await self._teardown_pipeline()
        self._video_process, self._audio_process = processes
        self._seek_base = start_seconds
        self._segment_started_at = time.monotonic()
        self._start_line = StartLine()
        self._video_task = asyncio.create_task(self._pump_video(self._video_process), name="jellyfin-video-pump")
        self._audio_task = asyncio.create_task(self._pump_audio(self._audio_process), name="jellyfin-audio-pump")

    async def _pump_video(self, process):
        """Reads fixed-size I420 frames and paces them to the title's frame rate; pausing just stops reading the pipe,
        so ffmpeg blocks on its own full pipe buffer instead of needing a separate pause signal."""
        rtc = self.voice_session.rtc
        width, height = self.quality.frame_size
        frame_size = frame_byte_size(width, height)
        frame_interval = 1.0 / float(self.fps)
        frame_index = 0
        start = None
        start_line = self._start_line
        while True:
            if self.paused:
                await asyncio.sleep(0.1)
                if start is not None:
                    start = time.monotonic() - frame_index * frame_interval
                continue
            try:
                chunk = await process.stdout.readexactly(frame_size)
            except asyncio.IncompleteReadError:
                start_line.video_gone()
                self.bot.background(self._video_ended(process), name=f"jellyfin-video-ended-{self.voice_channel_id}")
                return
            if start is None:
                await start_line.video_ready()
                start = self._segment_started_at = time.monotonic()
            frame = rtc.VideoFrame(width, height, rtc.VideoBufferType.I420, chunk)
            self._video_source.capture_frame(frame, timestamp_us=int(time.monotonic() * 1_000_000))
            frame_index += 1
            delay = (start + frame_index * frame_interval) - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)

    async def _pump_audio(self, process):
        """Ends quietly when the audio ffmpeg does: a title with no audio stream plays on in silence."""
        chunk_bytes = audio_chunk_bytes()
        chunk_samples = audio_chunk_samples()
        rtc = self.voice_session.rtc
        start_line = self._start_line
        started = False
        while True:
            if self.paused:
                await asyncio.sleep(0.1)
                continue
            try:
                chunk = await process.stdout.readexactly(chunk_bytes)
            except asyncio.IncompleteReadError:
                start_line.audio_gone()
                return
            if not started:
                await start_line.audio_ready()
                started = True
            frame = rtc.AudioFrame(chunk, AUDIO_SAMPLE_RATE, AUDIO_CHANNELS, chunk_samples)
            await self._audio_source.capture_frame(frame)

    async def _video_ended(self, process):
        """The video pipe closing is the title ending only if ffmpeg exited cleanly near the runtime; anything else dropped."""
        if self.finished or process is not self._video_process:
            return
        if await self._exit_code(process) == 0 and self._near_the_end():
            await self._handle_finished()
            return
        await self._end_after_drop()

    async def _exit_code(self, process):
        try:
            return await asyncio.wait_for(process.wait(), timeout=EXIT_WAIT_SECONDS)
        except asyncio.TimeoutError:
            return None

    def _near_the_end(self):
        """A title with no runtime on record cannot be checked, so a clean exit alone counts."""
        return not self.duration_seconds or self.duration_seconds - self.position_seconds <= END_TOLERANCE_SECONDS

    async def _end_after_drop(self):
        """Reports the position it really reached, never `finished`, so a dropped stream neither wipes resume nor marks it watched."""
        print(f"jellyfin stream for {self.title!r} ended at {format_hms(self.position_seconds)} of {format_hms(self.duration_seconds)}", file=sys.stderr)
        await self.stop(reason="the stream dropped")

    async def _teardown_pipeline(self):
        for task in (self._video_task, self._audio_task):
            if task is not None:
                task.cancel()
        for task in (self._video_task, self._audio_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        for process in (self._video_process, self._audio_process):
            if process is not None:
                await reap(process)
        if self._audio_source is not None:
            self._audio_source.clear_queue()
        self._video_task = self._audio_task = None
        self._video_process = self._audio_process = None

    def pause(self):
        if self.paused or self.finished:
            return False
        self._seek_base = self.position_seconds
        self.paused = True
        self._sync_changed()
        return True

    def resume(self):
        if not self.paused:
            return False
        self.paused = False
        self._segment_started_at = time.monotonic()
        self._sync_changed()
        return True

    async def seek(self, seconds):
        """Restarts the transcode at `seconds` clamped into the title; landing on the end finishes it."""
        seconds = max(0.0, seconds)
        if self.duration_seconds and seconds >= self.duration_seconds:
            await self._handle_finished()
            return
        await self._start_pipeline(seconds)
        self._sync_changed(seeked=True)

    async def set_quality(self, quality: Quality):
        """Republishes at the new size and ceilings, then resumes the transcode from the current position."""
        position = self.position_seconds
        previous = self.quality
        await self._teardown_pipeline()
        try:
            await self.voice_session.unpublish_screen_share()
            self.quality = quality
            await self._publish()
            await self._start_pipeline(position)
        except BaseException:
            await self._restore_quality(previous, position)
            raise

    async def _restore_quality(self, previous, position):
        """Best effort: a failed switch goes back to the old size rather than leaving the party silent."""
        self.quality = previous
        with contextlib.suppress(Exception):
            await self.voice_session.unpublish_screen_share()
            await self._publish()
            await self._start_pipeline(position)

    async def play_item(self, item, start_seconds=0.0):
        """Moves the party to another title on the same call, keeping the published share."""
        await self._report_progress(self.position_seconds, finished=self.ended_naturally)
        self._cancel_next_timer()
        await self._teardown_pipeline()
        self._set_item(item)
        self.waiting_next = None
        self.ended_naturally = False
        self.paused = False
        await self._start_pipeline(start_seconds)
        self._sync_changed()

    async def refresh_panel(self):
        if self.panel is not None:
            await self.panel.refresh(self)

    async def set_subtitle(self, stream_index, label):
        position = self.position_seconds
        self.subtitle_stream_index = stream_index
        self.subtitle_label = label
        await self.seek(position)

    def _cancel_next_timer(self):
        timer, self._next_timer = self._next_timer, None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    async def _upcoming_episode(self):
        """The next episode for the person who started this, or None (a movie, a finale, a Jellyfin hiccup)."""
        with contextlib.suppress(Exception):
            user_id = self.jellyfin_user_id or await asyncio.to_thread(playback_progress.resolve_user_id)
            return await asyncio.to_thread(jellyfin_core.next_episode, self.item, user_id)
        return None

    async def _hold_for_next(self, upcoming):
        """Keeps the call after an episode ends: plays the next one, or waits for a press before leaving."""
        await self._report_progress(self.duration_seconds, finished=True)
        if jellyfin_core.JELLYFIN_AUTOPLAY_NEXT:
            await self.play_item(upcoming)
            await self.refresh_panel()
            return
        self.waiting_next = upcoming
        self.paused = True
        self._seek_base = self.duration_seconds
        self._sync_changed()
        self._next_timer = self.bot.background(self._leave_after_wait(), name=f"jellyfin-next-{self.voice_channel_id}")
        await self.refresh_panel()
        with contextlib.suppress(Exception):
            await self.bot.client.send(
                self.text_channel_id, f"finished **{self.title}**. next up: **{upcoming.get('Name')}** - press Next episode on the panel.",
            )

    async def _leave_after_wait(self):
        await asyncio.sleep(jellyfin_core.JELLYFIN_NEXT_WAIT_SECONDS)
        if self.waiting_next is not None and not self.finished:
            await self._end_after_finish()

    async def _handle_finished(self):
        self.ended_naturally = True
        upcoming = await self._upcoming_episode()
        if upcoming is not None and not self.finished:
            await self._hold_for_next(upcoming)
            return
        await self._end_after_finish()

    async def _end_after_finish(self):
        self.finished = True
        text_channel_id, title = self.text_channel_id, self.title
        await self.stop(reason="finished", announce=False)
        with contextlib.suppress(Exception):
            await self.bot.client.send(text_channel_id, f"finished playing **{title}**.")

    async def _report_progress(self, seconds, *, finished=False):
        """Best-effort: a Jellyfin hiccup must never interrupt playback."""
        self._last_reported_at = time.monotonic()
        with contextlib.suppress(Exception):
            await asyncio.to_thread(
                playback_progress.report_position, self.item_id, seconds, finished=finished, user_id=self.jellyfin_user_id,
            )

    async def stop(self, *, reason="stopped", announce=True):
        final_position = self.position_seconds
        self.finished = True
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            self._monitor_task = None
        self._cancel_next_timer()
        await self._teardown_pipeline()
        await self._report_progress(final_position, finished=self.ended_naturally)
        if self.sync is not None:
            await self.sync.end()
        await self.voice_session.leave()
        if self.panel is not None:
            await self.panel.close(self, reason)
        if announce and self.panel is None:
            with contextlib.suppress(Exception):
                await self.bot.client.send(self.text_channel_id, f"stopped **{self.title}** ({reason}).")

    async def _monitor_loop(self):
        while True:
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake_monitor.wait(), timeout=ROSTER_POLL_SECONDS)
            self._wake_monitor.clear()
            if self.finished:
                return
            if self.waiting_next is None and playback_progress.report_due(self._last_reported_at):
                await self._report_progress(self.position_seconds)
            roster = await self.bot.client.voice_roster(self.voice_channel_id)
            others = [p for p in roster.get("participants", []) if p.get("user_id") != self.bot.me_id]
            if not others:
                await self.stop(reason="the call is empty")
                return

    def now_playing_embed(self):
        embed = Embed(title=self.title)
        embed.add_field("position", format_hms(self.position_seconds), inline=True)
        if self.duration_seconds:
            embed.add_field("duration", format_hms(self.duration_seconds), inline=True)
        embed.add_field("state", "paused" if self.paused else "playing", inline=True)
        embed.add_field("quality", self.quality.describe(), inline=True)
        if self.subtitle_label:
            embed.add_field("subtitles", self.subtitle_label, inline=True)
        return embed
