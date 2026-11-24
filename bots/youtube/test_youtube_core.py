#!/usr/bin/env python3
"""Link parsing and the metadata the bot reads; run directly: python3 test_youtube_core.py."""

import asyncio

import youtube_core
from slimbots.testing import run_module_tests
from slimbots.web import ServiceAuthError, WebResponse


def test_every_link_shape_gives_the_id_and_start():
    cases = {
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ": ("dQw4w9WgXcQ", 0),
        "youtube.com/watch?v=dQw4w9WgXcQ&t=1m30s": ("dQw4w9WgXcQ", 90),
        "https://youtu.be/dQw4w9WgXcQ?t=42": ("dQw4w9WgXcQ", 42),
        "<https://www.youtube.com/shorts/dQw4w9WgXcQ>": ("dQw4w9WgXcQ", 0),
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?start=7": ("dQw4w9WgXcQ", 7),
        "https://music.youtube.com/watch?v=dQw4w9WgXcQ": ("dQw4w9WgXcQ", 0),
        "https://m.youtube.com/live/dQw4w9WgXcQ": ("dQw4w9WgXcQ", 0),
    }
    for text, (video_id, start) in cases.items():
        assert youtube_core.parse_link(text) == youtube_core.Link(video_id, start), text


def test_anything_else_is_not_a_link():
    for text in ("", "never gonna", "https://vimeo.com/123", "https://www.youtube.com/@channel",
                 "https://www.youtube.com/watch?v=short", "javascript:alert(1)", "https://evil.example/watch?v=dQw4w9WgXcQ",
                 "https://youtu.be/dQw4w9WgXcQ extra"):
        assert youtube_core.parse_link(text) is None, text


def test_an_absurd_start_is_no_start():
    assert youtube_core.parse_link("https://youtu.be/dQw4w9WgXcQ?t=9999999").start_seconds == 0
    assert youtube_core.parse_link("https://youtu.be/dQw4w9WgXcQ?t=abc").start_seconds == 0


def test_the_page_length_comes_from_its_own_metadata():
    assert youtube_core.duration_from_page('<meta itemprop="duration" content="PT3M34S">') == 214
    assert youtube_core.duration_from_page('"lengthSeconds":"19"') == 19
    assert youtube_core.duration_from_page("<html></html>") is None
    assert youtube_core.parse_iso_duration("P1DT2H") == 93600


class FakeService:
    def __init__(self, oembed_status=200, page="", oembed=None):
        self.oembed_status = oembed_status
        self.page = page
        self.oembed = oembed or b'{"title": "A Clip", "author_name": "Someone"}'

    async def send(self, method, url, **_kwargs):
        return WebResponse(self.oembed_status, {}, self.oembed, url)

    async def peek(self, url, **_kwargs):
        return WebResponse(200, {}, self.page.encode(), url)


def test_a_lookup_reads_title_channel_and_length():
    lookup = youtube_core.Lookup(user_agent="t", service=FakeService(page='<meta itemprop="duration" content="PT19S">'))
    video = asyncio.run(lookup.video("jNQXAC9IVRw"))
    assert (video.title, video.channel, video.duration_seconds) == ("A Clip", "Someone", 19)


def test_embedding_turned_off_and_a_missing_video_are_told_apart():
    for status, error in ((401, youtube_core.EmbedRefused), (403, youtube_core.EmbedRefused), (404, youtube_core.NoSuchVideo)):
        lookup = youtube_core.Lookup(user_agent="t", service=FakeService(oembed_status=status))
        try:
            asyncio.run(lookup.video("jNQXAC9IVRw"))
        except error:
            continue
        raise AssertionError(f"HTTP {status} did not raise {error.__name__}")


class KeyRefusedService(FakeService):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.data_api_calls = 0

    async def get(self, url, **_kwargs):
        self.data_api_calls += 1
        raise ServiceAuthError("youtube answered HTTP 403", 403)


def test_a_refused_api_key_logs_once_and_falls_back_to_oembed():
    service = KeyRefusedService(page='<meta itemprop="duration" content="PT19S">')
    lookup = youtube_core.Lookup(user_agent="t", api_key="bad", service=service)
    with _captured_warnings() as records:
        for _ in range(3):
            video = asyncio.run(lookup.video("jNQXAC9IVRw"))
            assert (video.title, video.duration_seconds) == ("A Clip", 19)
    assert service.data_api_calls == 3
    assert len(records) == 1 and "YOUTUBE_API_KEY" in records[0]


class _captured_warnings:
    def __enter__(self):
        import logging
        self.records = []
        outer = self.records

        class Collect(logging.Handler):
            def emit(self, record):
                outer.append(record.getMessage())

        self.logger = logging.getLogger("youtube.core")
        self.handler = Collect(logging.WARNING)
        self.logger.addHandler(self.handler)
        return self.records

    def __exit__(self, *exc):
        self.logger.removeHandler(self.handler)


if __name__ == "__main__":
    run_module_tests(globals())
