#!/usr/bin/env python3
"""A Jellyfin track reaches ffmpeg through a loopback relay that holds the key; run directly: python3 test_jellyfin_relay.py."""

import asyncio
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bot as music
import httpx
import jellyfin_relay
import music_cog
import music_core
import player
from slimbots.testing import run_module_tests
from test_bot import scenario, setup, until

REAL_BUILD_ARGS = player.build_ffmpeg_args
KEY = "jf-secret-key"
TRACK_URL = "https://jf.example/Audio/a1/stream?Static=true"


def jellyfin_answers(seen, body=b"\x01" * player.CHUNK_BYTES * 3, status=200, extra=None):
    """A fake Jellyfin that records what each request carried."""

    def answer(request):
        seen.append(request)
        return httpx.Response(status, headers={"content-type": "audio/flac", **(extra or {})}, stream=httpx.ByteStream(body))

    return httpx.MockTransport(answer)


def fetching_ffmpeg(track, source=None):
    """A stand-in decoder that reads its input the way ffmpeg does: from the url it was given."""
    code = f"import sys, urllib.request\nsys.stdout.buffer.write(urllib.request.urlopen({source!r}).read())"
    return [sys.executable, "-c", code]


def test_a_jellyfin_track_plays_through_the_relay_and_ffmpeg_never_holds_the_key():
    client = setup(jellyfin=True)
    music_core.JELLYFIN_API_KEY = KEY
    seen, argv = [], []
    music_core.TRANSPORT = jellyfin_answers(seen)

    def recording(track, source=None):
        argv.append(REAL_BUILD_ARGS(track, source))
        return fetching_ffmpeg(track, source)

    async def fake_search(query, limit=5):
        return [{"Id": "a1", "Name": "Song", "AlbumArtist": "Band"}]

    held = []

    async def played():
        await until(lambda: music_cog.active_sessions().get("v1") is not None)
        held.append(music_cog.active_sessions()["v1"])
        await until(lambda: music.bot.voice.sessions and music.bot.voice.sessions[0].left, timeout=10)

    original, player.build_ffmpeg_args, music_core.search_tracks = music_core.search_tracks, recording, fake_search
    shutil_which, shutil.which = shutil.which, lambda name: "/usr/bin/ffmpeg"
    try:
        scenario(client, "~play band song", played)
    finally:
        music_core.search_tracks, shutil.which = original, shutil_which
    (args,) = argv
    assert held[0]._source.frame_count == 3
    assert not [a for a in args if KEY in a or "MediaBrowser" in a], args
    assert re.fullmatch(r"http://127\.0\.0\.1:\d+/[\w-]{32}", args[args.index("-i") + 1])
    assert [r.headers["authorization"] for r in seen] == [f'MediaBrowser Token="{KEY}"']
    assert str(seen[0].url) == "https://jf.example/Audio/a1/stream?Static=true"


async def relay_get(relay, path=None, method="GET", headers=None):
    async with httpx.AsyncClient() as http:
        return await http.request(method, relay.url if path is None else relay.url.rsplit("/", 1)[0] + path, headers=headers)


def in_relay(transport, body):
    music_core.TRANSPORT = transport

    async def run():
        async with jellyfin_relay.JellyfinRelay(TRACK_URL) as relay:
            return await body(relay)

    return asyncio.run(run())


def test_the_relay_adds_the_key_and_passes_a_range_through_both_ways():
    setup(jellyfin=True)
    music_core.JELLYFIN_API_KEY = KEY
    seen = []
    transport = jellyfin_answers(seen, body=b"tail", status=206, extra={"content-range": "bytes 6-9/10", "accept-ranges": "bytes"})
    answer = in_relay(transport, lambda relay: relay_get(relay, headers={"range": "bytes=6-"}))
    assert answer.status_code == 206 and answer.content == b"tail"
    assert answer.headers["content-range"] == "bytes 6-9/10" and answer.headers["accept-ranges"] == "bytes"
    assert seen[0].headers["range"] == "bytes=6-" and seen[0].headers["authorization"] == f'MediaBrowser Token="{KEY}"'
    assert KEY not in str(answer.headers)


def test_the_relay_answers_only_its_own_path_and_only_to_a_get():
    setup(jellyfin=True)
    seen = []

    async def probes(relay):
        return [(await relay_get(relay, path="/guess")).status_code, (await relay_get(relay, method="POST")).status_code]

    assert in_relay(jellyfin_answers(seen), probes) == [404, 404]
    assert seen == []


def test_the_relay_never_hands_ffmpeg_a_redirect_or_a_malformed_range():
    setup(jellyfin=True)
    seen = []
    transport = jellyfin_answers(seen, status=302, extra={"location": "http://10.0.0.5/elsewhere"})
    answer = in_relay(transport, lambda relay: relay_get(relay, headers={"range": "bytes=0-1, ../etc"}))
    assert answer.status_code == 502 and "location" not in answer.headers
    assert len(seen) == 1 and "range" not in seen[0].headers


class RangedFile(BaseHTTPRequestHandler):
    """A Jellyfin stand-in that honours Range the way Jellyfin's static stream does, so ffmpeg can seek."""

    body = b""
    keys = []

    def do_GET(self):
        RangedFile.keys.append(self.headers.get("Authorization", ""))
        start, end, size = 0, len(self.body) - 1, len(self.body)
        wanted = re.fullmatch(r"bytes=(\d+)-(\d*)", self.headers.get("Range", ""))
        if wanted:
            start, end = int(wanted[1]), int(wanted[2] or end)
        self.send_response(206 if wanted else 200)
        self.send_header("Content-Type", "audio/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        if wanted:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        self.wfile.write(self.body[start:end + 1])

    def log_message(self, *_args):
        return None


def make_m4a_with_the_index_last(directory):
    """Thirty seconds of AAC with its moov atom after the audio, which a pipe cannot decode; None without a real ffmpeg."""
    binary = shutil.which("ffmpeg")
    path = f"{directory}/tone.m4a"
    command = [binary, "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=30", "-c:a", "aac", "-b:a", "320k", path]
    if binary is None or subprocess.run(command, capture_output=True, timeout=60, check=False).returncode != 0:
        return None
    with open(path, "rb") as handle:
        return handle.read()


def test_a_real_ffmpeg_seeks_through_the_relay_into_an_m4a_whose_index_comes_last():
    with tempfile.TemporaryDirectory(prefix="music-relay-") as directory:
        audio = make_m4a_with_the_index_last(directory)
    if audio is None:
        print("  (no ffmpeg on PATH; the real-ffmpeg check is skipped)")
        return
    setup(jellyfin=True)
    music_core.JELLYFIN_API_KEY = KEY
    RangedFile.body, RangedFile.keys = audio, []
    server = ThreadingHTTPServer(("127.0.0.1", 0), RangedFile)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    music_core.TRANSPORT = None
    track = music_core.Track("tone", f"http://127.0.0.1:{server.server_port}/Audio/a1/stream", jellyfin=True)

    async def decode():
        async with jellyfin_relay.JellyfinRelay(track.url) as relay:
            process = await asyncio.create_subprocess_exec(
                *REAL_BUILD_ARGS(track, relay.url), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            return await asyncio.wait_for(process.communicate(), timeout=60)

    try:
        pcm, err = asyncio.run(decode())
    finally:
        server.shutdown()
    assert len(pcm) >= 29 * player.AUDIO_SAMPLE_RATE * player.AUDIO_CHANNELS * 2, err
    assert len(RangedFile.keys) > 1 and all(k == f'MediaBrowser Token="{KEY}"' for k in RangedFile.keys)


if __name__ == "__main__":
    run_module_tests(globals())
