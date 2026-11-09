#!/usr/bin/env python3
"""The transcode reaches ffmpeg on stdin, so the API key is never in its command line; run directly: python3 test_transcode_feed.py."""

import asyncio
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

import jellyfin_core
import quality
import session_registry
import stream_session
import transcode_feed
from slimbots.testing import FakeVoiceSession, run_module_tests
from test_bot import jellyfin, setup_with_voice
from test_resume import Patched

KEY_HEADER = 'MediaBrowser Token="fake-key"'
VIDEO_ONLY = {"Id": "m1", "Name": "film", "Type": "Movie", "RunTimeTicks": 1_000_000, "MediaStreams": [{"Type": "Video", "Index": 0}]}


class FakeStdin:
    """What `feed` writes to: records bytes, can hold `drain` closed, and notes when it is closed."""

    def __init__(self, hold_drain=False, keep=True):
        self.written = []
        self.keep = keep
        self.closed = False
        self._open = asyncio.Event()
        if not hold_drain:
            self._open.set()

    def write(self, chunk):
        if self.keep:
            self.written.append(chunk)

    async def drain(self):
        await self._open.wait()
        await asyncio.sleep(0)

    def close(self):
        self.closed = True

    def release(self):
        self._open.set()


class Body(httpx.AsyncByteStream):
    """A response body that is still to be streamed, as a real one is; `content=` would arrive already read."""

    def __init__(self, *chunks):
        self.chunks = chunks

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk


def served(handler):
    return Patched((jellyfin_core, "TRANSPORT", httpx.MockTransport(handler)))


def run_feed(handler, stdin=None, url="https://fake-jellyfin.invalid/Videos/m1/stream?Static=false"):
    stdin = stdin or FakeStdin()
    with served(handler):
        asyncio.run(asyncio.wait_for(transcode_feed.feed(url, stdin), timeout=5))
    return stdin


def test_the_body_goes_to_ffmpeg_and_the_key_goes_in_a_header_not_the_address():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, stream=Body(*[b"abc" * 5_000] * 10))

    stdin = run_feed(handler)
    assert b"".join(stdin.written) == b"abc" * 50_000 and stdin.closed
    request = seen[0]
    assert request.headers["authorization"] == KEY_HEADER and request.headers["user-agent"] == jellyfin_core.USER_AGENT
    assert request.headers["accept-encoding"] == "identity", "a compressed body would reach ffmpeg compressed"
    assert "fake-key" not in str(request.url)


def test_a_refusal_or_a_dead_connection_closes_ffmpegs_input_so_the_start_fails_in_the_usual_way():
    stdin = run_feed(lambda request: httpx.Response(401))
    assert stdin.written == [] and stdin.closed

    def unreachable(request):
        raise httpx.ConnectError("refused", request=request)

    stdin = run_feed(unreachable)
    assert stdin.written == [] and stdin.closed


def test_a_redirect_is_not_followed_so_the_key_is_never_replayed_elsewhere():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://elsewhere.invalid/stolen"})

    stdin = run_feed(handler)
    assert calls == ["https://fake-jellyfin.invalid/Videos/m1/stream?Static=false"] and stdin.written == [] and stdin.closed


def test_a_cut_connection_mid_body_ends_the_input_after_what_arrived():
    class Cut(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"first"
            raise httpx.ReadError("connection reset")

    stdin = run_feed(lambda request: httpx.Response(200, stream=Cut()))
    assert stdin.written == [b"first"] and stdin.closed


def test_ffmpeg_not_reading_holds_the_download_back_instead_of_buffering_it():
    pulled = []

    chunk = b"x" * 1024

    class Endless(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                pulled.append(1)
                yield chunk

    async def scenario():
        stdin = FakeStdin(hold_drain=True, keep=False)
        with served(lambda request: httpx.Response(200, stream=Endless())):
            task = transcode_feed.start("https://fake-jellyfin.invalid/x", stdin)
            await asyncio.sleep(0.2)
            while_blocked = len(pulled)
            stdin.release()
            await asyncio.sleep(0.2)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return while_blocked, stdin

    while_blocked, stdin = asyncio.run(scenario())
    assert while_blocked <= 2, "a paused party must stop pulling from Jellyfin"
    assert len(pulled) > while_blocked and stdin.closed


def test_cancelling_the_feed_closes_the_input_even_in_the_middle_of_a_body():
    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"some"
            await asyncio.Event().wait()

    async def scenario():
        stdin = FakeStdin()
        with served(lambda request: httpx.Response(200, stream=Stalled())):
            task = transcode_feed.start("https://fake-jellyfin.invalid/x", stdin)
            await asyncio.sleep(0.1)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        return stdin

    stdin = asyncio.run(scenario())
    assert stdin.written == [b"some"] and stdin.closed


class FakeJellyfinServer(BaseHTTPRequestHandler):
    """Answers a transcode request the way Jellyfin does: chunked, no length, and only for the right key."""

    body = b""
    seen = []

    def do_GET(self):
        FakeJellyfinServer.seen.append((self.path, self.headers.get("Authorization")))
        if self.headers.get("Authorization") != KEY_HEADER:
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "video/x-matroska")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for start in range(0, len(self.body), 8192):
            piece = self.body[start:start + 8192]
            self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
        self.wfile.write(b"0\r\n\r\n")

    def log_message(self, *_args):
        return None


def quiet_pumps():
    """The test reads ffmpeg's output itself, so the session's own pumps must not."""
    return [(stream_session.WatchSession, name, lambda self, *_a: asyncio.sleep(0)) for name in ("_pump_video", "_pump_audio")]


def make_matroska():
    """A short video-only stream written to a pipe, so it is as unseekable as a live transcode."""
    command = [
        shutil.which("ffmpeg"), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=10",
        "-t", "2", "-c:v", "mpeg4", "-f", "matroska", "pipe:1",
    ]
    done = subprocess.run(command, capture_output=True, timeout=60, check=False)
    return done.stdout if done.returncode == 0 else b""


def test_a_real_ffmpeg_plays_the_transcode_off_stdin_with_no_key_on_its_command_line():
    body = make_matroska() if shutil.which("ffmpeg") else b""
    if not body:
        print("  (no ffmpeg with an mpeg4 encoder on PATH; the real-ffmpeg check is skipped)")
        return
    FakeJellyfinServer.body, FakeJellyfinServer.seen = body, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeJellyfinServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    setup_with_voice()

    async def scenario():
        session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", dict(VIDEO_ONLY), "u1", FakeVoiceSession("c1"))
        session.quality = quality.Quality("tiny", 16, 16, 100_000)
        session_registry.add(session)
        await session._publish()
        await session._start_pipeline(0.0)
        process = session._video_process
        with open(f"/proc/{process.pid}/cmdline", "rb") as handle:
            argv = handle.read().decode(errors="replace")
        frame = stream_session.frame_byte_size(16, 16)
        frames = 0
        try:
            while True:
                await asyncio.wait_for(process.stdout.readexactly(frame), timeout=20)
                frames += 1
        except asyncio.IncompleteReadError:
            pass
        await session._teardown_pipeline()
        return argv, frames, await process.wait()

    try:
        with Patched((jellyfin_core, "JELLYFIN_URL", f"http://127.0.0.1:{server.server_port}"), *quiet_pumps()):
            argv, frames, code = asyncio.run(scenario())
    finally:
        server.shutdown()
    assert frames >= 15, f"ffmpeg decoded {frames} frames of a 2 second, 10 fps stream"
    assert "fake-key" not in argv and str(server.server_port) not in argv and "Authorization" not in argv
    assert FakeJellyfinServer.seen and all(header == KEY_HEADER for _path, header in FakeJellyfinServer.seen)
    assert "StartTimeTicks=0" in FakeJellyfinServer.seen[0][0] and os.path.basename(argv.split("\0")[0]) == "ffmpeg"


def test_a_real_ffmpeg_given_a_refused_request_ends_instead_of_hanging():
    body = make_matroska() if shutil.which("ffmpeg") else b""
    if not body:
        print("  (no ffmpeg with an mpeg4 encoder on PATH; the real-ffmpeg check is skipped)")
        return
    FakeJellyfinServer.body, FakeJellyfinServer.seen = body, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeJellyfinServer)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    setup_with_voice()

    async def scenario():
        session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", dict(VIDEO_ONLY), "u1", FakeVoiceSession("c1"))
        session.quality = quality.Quality("tiny", 16, 16, 100_000)
        await session._publish()
        await session._start_pipeline(0.0)
        process = session._video_process
        frames = 0
        try:
            await asyncio.wait_for(process.stdout.readexactly(stream_session.frame_byte_size(16, 16)), timeout=20)
            frames = 1
        except asyncio.IncompleteReadError:
            pass
        await session._teardown_pipeline()
        return frames

    try:
        with Patched(
            (jellyfin_core, "JELLYFIN_URL", f"http://127.0.0.1:{server.server_port}"), (jellyfin_core, "JELLYFIN_API_KEY", "wrong-key"), *quiet_pumps(),
        ):
            frames = asyncio.run(scenario())
    finally:
        server.shutdown()
    assert frames == 0 and FakeJellyfinServer.seen[0][1] != KEY_HEADER


if __name__ == "__main__":
    run_module_tests(globals())
