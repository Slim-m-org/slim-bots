#!/usr/bin/env python3
"""A direct stream reaches ffmpeg on stdin through the same public-only policy that vetted it; run directly: python3 test_stream_feed.py."""

import asyncio
import contextlib
import io
import shutil
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bot as music
import httpx
import music_core
import player
import stream_feed
from slimbots.testing import run_module_tests
from test_bot import PUBLIC_URL, scenario, setup, until

REAL_BUILD_ARGS = player.build_ffmpeg_args
LAN = "http://10.0.0.5:8123/reboot"


class FakeStdin:
    """What `feed` writes to: records bytes, notes when it is closed, and can behave like the pipe of an ffmpeg that has exited."""

    def __init__(self, gone=False):
        self.written = []
        self.closed = False
        self.gone = gone

    def write(self, chunk):
        self.written.append(chunk)

    async def drain(self):
        if self.gone:
            raise BrokenPipeError()
        await asyncio.sleep(0)

    def close(self):
        self.closed = True

    @property
    def data(self):
        return b"".join(self.written)


class Body(httpx.AsyncByteStream):
    """A body still to be streamed, as a real one is, optionally cut by a read error once its chunks are out."""

    def __init__(self, *chunks, cut=False):
        self.chunks = chunks
        self.cut = cut

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.cut:
            raise httpx.ReadError("connection reset")


def serving(handler):
    """Points the stream policy at `handler`, returning every request it saw."""
    setup()
    seen = []

    def record(request):
        seen.append(request)
        return handler(request)

    music_core.TRANSPORT = httpx.MockTransport(record)
    return seen


def run_feed(url=PUBLIC_URL, stdin=None):
    """The fed input, with what the feed printed to stderr on `.stderr`."""
    stdin = stdin or FakeStdin()
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        asyncio.run(asyncio.wait_for(stream_feed.feed(url, stdin), timeout=5))
    stdin.stderr = stderr.getvalue()
    return stdin


def test_the_body_goes_to_ffmpegs_stdin_after_the_redirects_and_the_input_is_closed():
    def handler(request):
        if request.url.path == "/moved":
            return httpx.Response(302, headers={"location": "https://93.184.216.35/final.mp3"})
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, stream=Body(b"ID3", b"audio", b"bytes"))

    seen = serving(handler)
    stdin = run_feed("https://93.184.216.34/moved")
    assert stdin.data == b"ID3audiobytes" and stdin.closed
    last = seen[-1]
    assert last.headers["user-agent"] == music_core.USER_AGENT and last.headers["accept-encoding"] == "identity"
    assert "authorization" not in last.headers


def test_a_redirect_into_the_lan_at_play_time_is_refused_and_never_requested():
    seen = serving(lambda request: httpx.Response(302, headers={"location": LAN}))
    stdin = run_feed()
    assert len(seen) == 1 and stdin.written == [] and stdin.closed and "not publicly routable" in stdin.stderr


def test_a_host_that_turns_private_after_it_was_vetted_is_refused_when_playback_opens_it():
    answers = ["93.184.216.34"]

    async def resolver(host):
        return list(answers)

    seen = serving(lambda request: httpx.Response(200, stream=Body(b"ID3" + b"\x00" * 600)))
    music_core.RESOLVER = resolver
    try:
        track = asyncio.run(music_core.open_stream("https://radio.example/live.mp3"))
        assert seen[0].url.host == "93.184.216.34" and seen[0].headers["host"] == "radio.example"
        answers[:] = ["10.0.0.5"]
        stdin = run_feed(track.url)
    finally:
        music_core.RESOLVER = None
    assert len(seen) == 1 and stdin.written == [] and stdin.closed and "not publicly routable" in stdin.stderr


def test_a_stream_that_stops_answering_well_closes_the_input_without_writing():
    seen = serving(lambda request: httpx.Response(404))
    stdin = run_feed()
    assert len(seen) == 1 and stdin.written == [] and stdin.closed and "HTTP 404" in stdin.stderr


def test_a_live_stream_that_drops_is_opened_again_and_carries_on():
    bodies = iter([Body(b"abc", cut=True), Body(b"def")])
    seen = serving(lambda request: httpx.Response(200, stream=next(bodies)))
    original = stream_feed.RECONNECT_DELAY_SECONDS
    stream_feed.RECONNECT_DELAY_SECONDS = 0
    try:
        stdin = run_feed()
    finally:
        stream_feed.RECONNECT_DELAY_SECONDS = original
    assert stdin.data == b"abcdef" and stdin.closed and len(seen) == 2


def test_a_file_with_a_length_that_is_cut_ends_instead_of_starting_over():
    seen = serving(lambda request: httpx.Response(200, headers={"content-length": "100"}, stream=Body(b"abc", cut=True)))
    stdin = run_feed()
    assert stdin.data == b"abc" and stdin.closed and len(seen) == 1


def test_a_live_stream_that_keeps_dropping_before_a_byte_is_given_up_on():
    seen = serving(lambda request: httpx.Response(200, stream=Body(cut=True)))
    original = stream_feed.RECONNECT_DELAY_SECONDS
    stream_feed.RECONNECT_DELAY_SECONDS = 0
    try:
        stdin = run_feed()
    finally:
        stream_feed.RECONNECT_DELAY_SECONDS = original
    assert stdin.written == [] and stdin.closed and len(seen) == stream_feed.RECONNECTS + 1
    assert "gave up" in stdin.stderr


def test_an_ffmpeg_that_has_gone_ends_the_feed_quietly_and_closes_the_input():
    serving(lambda request: httpx.Response(200, stream=Body(b"abc", b"def")))
    stdin = run_feed(stdin=FakeStdin(gone=True))
    assert stdin.closed and stdin.stderr == ""


def test_cancelling_the_feed_closes_the_input_even_in_the_middle_of_a_body():
    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"some"
            await asyncio.Event().wait()

    serving(lambda request: httpx.Response(200, stream=Stalled()))

    async def scenario_():
        stdin = FakeStdin()
        task = stream_feed.start(PUBLIC_URL, stdin)
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        return stdin

    stdin = asyncio.run(scenario_())
    assert stdin.data == b"some" and stdin.closed


def make_wav(directory):
    """Two seconds of tone from the real ffmpeg, or None when there is none to make it."""
    binary = shutil.which("ffmpeg")
    path = f"{directory}/tone.wav"
    command = [binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2", path]
    if binary is None or subprocess.run(command, capture_output=True, timeout=60, check=False).returncode != 0:
        return None
    with open(path, "rb") as handle:
        return handle.read()


class Radio(BaseHTTPRequestHandler):
    """Answers the bot's own request with audio and anything else, which is what ffmpeg sends, with a redirect to the second server."""

    audio = b""
    elsewhere = ""
    seen = []

    def do_GET(self):
        Radio.seen.append(self.headers.get("User-Agent", ""))
        if "slimm-bot-music" not in self.headers.get("User-Agent", ""):
            self.send_response(302)
            self.send_header("Location", Radio.elsewhere)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(Radio.audio)))
        self.end_headers()
        self.wfile.write(Radio.audio)

    def log_message(self, *_args):
        return None


class Lan(BaseHTTPRequestHandler):
    """The host a redirect aimed at ffmpeg would reach."""

    hits = []

    def do_GET(self):
        Lan.hits.append(self.path)
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *_args):
        return None


def test_a_real_ffmpeg_decodes_a_direct_stream_off_stdin_and_never_contacts_the_url_itself():
    with tempfile.TemporaryDirectory(prefix="music-feed-") as directory:
        audio = make_wav(directory)
    if audio is None:
        print("  (no ffmpeg on PATH; the real-ffmpeg check is skipped)")
        return
    lan = ThreadingHTTPServer(("127.0.0.1", 0), Lan)
    radio = ThreadingHTTPServer(("127.0.0.1", 0), Radio)
    Radio.audio, Radio.seen, Lan.hits = audio, [], []
    Radio.elsewhere = f"http://127.0.0.1:{lan.server_port}/reboot"
    for server in (lan, radio):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    client = setup()
    music_core.TRANSPORT = None
    music_core.JELLYFIN_URL, music_core.JELLYFIN_API_KEY = f"http://127.0.0.1:{radio.server_port}", "key"
    player.build_ffmpeg_args = REAL_BUILD_ARGS

    async def finished():
        await until(lambda: music.bot.voice.sessions[0].left, timeout=20)

    try:
        scenario(client, f"~play http://127.0.0.1:{radio.server_port}/tone.wav", finished)
    finally:
        for server in (radio, lan):
            server.shutdown()
            server.server_close()
    source = music.bot.voice.sessions[0].room.local_participant.published[0][0]["source"]
    assert source.frame_count >= 90, f"ffmpeg decoded {source.frame_count} chunks of a 2 second tone"
    assert Radio.seen and all("slimm-bot-music" in agent for agent in Radio.seen), f"someone else fetched the url: {Radio.seen}"
    assert Lan.hits == [], "a redirect meant for ffmpeg reached the second host"


if __name__ == "__main__":
    run_module_tests(globals())
