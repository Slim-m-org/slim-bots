"""YouTube links and what the bot may learn about a video: its id, start time, title, channel and length.
Only public metadata is read (oEmbed, the watch page's own `itemprop` tags, or the Data API with a key); nothing ever touches a stream."""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from slimbots.web import JsonService, ServiceError

_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}
_SHORT_HOSTS = {"youtu.be", "www.youtu.be"}
_PATH_KINDS = ("shorts", "embed", "live", "v")
_STAMP = re.compile(r"^(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s?)?$")
_ISO = re.compile(r"^P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$")
_ITEMPROP = re.compile(r'<meta\s+itemprop="duration"\s+content="([^"]+)"')
_LENGTH = re.compile(r'"lengthSeconds":"(\d+)"')
MAX_START_SECONDS = 360_000
MAX_DURATION_SECONDS = 7 * 24 * 3600
WATCH_PAGE_BYTES = 1536 * 1024


@dataclass(frozen=True)
class Link:
    video_id: str
    start_seconds: int = 0


@dataclass(frozen=True)
class Video:
    video_id: str
    title: str
    channel: str | None = None
    duration_seconds: int | None = None
    queued_by_id: str | None = None
    queued_by_name: str | None = None
    start_seconds: int = 0

    @property
    def url(self):
        return watch_url(self.video_id)


class EmbedRefused(Exception):
    """The video's owner turned embedding off, so it can only play on YouTube itself."""


class NoSuchVideo(Exception):
    """Private, removed, or never existed."""


def watch_url(video_id, start_seconds=0):
    stamp = f"&t={start_seconds}s" if start_seconds > 0 else ""
    return f"https://www.youtube.com/watch?v={video_id}{stamp}"


def parse_stamp(raw):
    """`t=90`, `t=1m30s` or `t=1h2m3s` as seconds; None for anything else, or past 100 hours."""
    match = _STAMP.match((raw or "").strip().lower())
    if not raw or match is None or not any(match.groups()):
        return None
    hours, minutes, seconds = (int(part or 0) for part in match.groups())
    total = hours * 3600 + minutes * 60 + seconds
    return total if total <= MAX_START_SECONDS else None


def _id_from(parsed):
    host = (parsed.hostname or "").lower()
    if host in _SHORT_HOSTS:
        return parsed.path.strip("/").split("/")[0]
    if host not in _HOSTS:
        return None
    segments = [s for s in parsed.path.split("/") if s]
    if segments[:1] == ["watch"]:
        return (parse_qs(parsed.query).get("v") or [""])[0]
    if len(segments) >= 2 and segments[0] in _PATH_KINDS:
        return segments[1]
    return None


def parse_link(text):
    """A watch, youtu.be, shorts, embed or live link (with or without the scheme), else None."""
    raw = (text or "").strip().strip("<>")
    if not raw or any(c.isspace() for c in raw):
        return None
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urlparse(raw)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    video_id = _id_from(parsed)
    if not video_id or not _ID.match(video_id):
        return None
    query = parse_qs(parsed.query)
    start = parse_stamp((query.get("t") or query.get("start") or [""])[0]) or 0
    return Link(video_id, start)


def parse_iso_duration(raw):
    """`PT4M13S` (or `P0DT1H2M3S`) as seconds; None when it is not one."""
    match = _ISO.match((raw or "").strip())
    if not raw or match is None or not any(match.groups()):
        return None
    days, hours, minutes, seconds = (int(part or 0) for part in match.groups())
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def duration_from_page(html):
    """The length a watch page states about itself, from its own metadata; None when it states none."""
    match = _ITEMPROP.search(html)
    seconds = parse_iso_duration(match.group(1)) if match else None
    if seconds is None:
        length = _LENGTH.search(html)
        seconds = int(length.group(1)) if length else None
    return seconds if seconds and 0 < seconds <= MAX_DURATION_SECONDS else None


class Lookup:
    """Fetches a video's title, channel and length; one instance per bot, built from its settings."""

    def __init__(self, *, user_agent, api_key="", service=None):
        self.api_key = api_key
        self.service = service or JsonService("youtube", user_agent=user_agent, public_only=True)

    async def video(self, video_id):
        """The video's details; raises EmbedRefused or NoSuchVideo, or ServiceError when YouTube cannot be reached."""
        if self.api_key:
            return await self._from_data_api(video_id)
        details = await self._oembed(video_id)
        duration = await self._page_duration(video_id)
        return Video(video_id, details["title"], details.get("author_name"), duration)

    async def _oembed(self, video_id):
        response = await self.service.send(
            "GET", "https://www.youtube.com/oembed", params={"url": watch_url(video_id), "format": "json"},
        )
        if response.status in (401, 403):
            raise EmbedRefused(video_id)
        if response.status in (400, 404):
            raise NoSuchVideo(video_id)
        if response.status >= 300:
            raise ServiceError(f"youtube answered HTTP {response.status}", response.status)
        try:
            body = response.json()
        except ValueError:
            raise ServiceError("youtube sent a body that is not JSON") from None
        if not isinstance(body, dict) or not isinstance(body.get("title"), str):
            raise ServiceError("youtube sent no title")
        return body

    async def _page_duration(self, video_id):
        """Best effort: a page that will not load or states no length just leaves the video without one."""
        try:
            response = await self.service.peek(watch_url(video_id), size=WATCH_PAGE_BYTES, headers={"accept-language": "en"})
        except ServiceError:
            return None
        if response.status >= 300:
            return None
        return duration_from_page(response.body.decode("utf-8", "replace"))

    async def _from_data_api(self, video_id):
        body = await self.service.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"id": video_id, "part": "snippet,contentDetails,status", "key": self.api_key},
        )
        items = body.get("items") if isinstance(body, dict) else None
        if not items:
            raise NoSuchVideo(video_id)
        item = items[0]
        if not item.get("status", {}).get("embeddable", True):
            raise EmbedRefused(video_id)
        snippet = item.get("snippet", {})
        duration = parse_iso_duration(item.get("contentDetails", {}).get("duration", ""))
        return Video(video_id, snippet.get("title") or video_id, snippet.get("channelTitle"), duration)
