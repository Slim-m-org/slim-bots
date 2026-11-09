"""bot-music's settings, track model, Jellyfin audio search and stream-URL vetting; see README.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" in docs/framework.md."""

import urllib.parse
from dataclasses import dataclass

from slimbots import jellyfin as jellyfin_api
from slimbots.jellyfin import JellyfinAuthError  # noqa: F401 - re-exported for the cog
from slimbots.urlpolicy import PRIVATE, host_of
from slimbots.web import JsonService, ServiceBlocked, ServiceError

MAX_QUERY_LENGTH = 200
MAX_QUEUE_LENGTH = 50
MAX_SEARCH_RESULTS = 5
USER_AGENT = "slimm-bot-music/1.0"
AUDIO_FIELDS = "Album,AlbumArtist,Artists,RunTimeTicks"
STREAM_PEEK_BYTES = 512
STREAM_TIMEOUT_SECONDS = 10
STREAM_DEADLINE_SECONDS = 15
PLAYLIST_TYPES = ("mpegurl", "scpls", "pls+xml", "dash+xml", "x-mpd")
PLAYLIST_SUFFIXES = (".m3u8", ".m3u", ".pls", ".mpd")
PLAYLIST_HEADS = (b"#extm3u", b"[playlist]", b"<?xml", b"<mpd")

# Populated once by configure(); empty JELLYFIN_URL means direct URLs only.
JELLYFIN_URL = ""
JELLYFIN_API_KEY = ""
# A test points this at an httpx transport instead of the network.
TRANSPORT = None


@dataclass
class Track:
    title: str
    url: str
    duration_seconds: float = 0.0
    headers: str | None = None

    @property
    def is_direct(self):
        """A member-supplied stream URL, as opposed to a Jellyfin track that carries the key."""
        return self.headers is None


def configure(bot):
    global JELLYFIN_URL, JELLYFIN_API_KEY
    JELLYFIN_URL = (bot.setting("JELLYFIN_URL", "") or "").rstrip("/")
    JELLYFIN_API_KEY = bot.setting("JELLYFIN_API_KEY", "") or ""


def jellyfin_enabled():
    return bool(JELLYFIN_URL and JELLYFIN_API_KEY)


def check_config():
    """A plaintext, non-loopback JELLYFIN_URL leaks the API key; a URL without a key is a half-configured bot."""
    if bool(JELLYFIN_URL) != bool(JELLYFIN_API_KEY):
        return "set both JELLYFIN_URL and JELLYFIN_API_KEY, or neither for direct-URL-only playback"
    return jellyfin_api.url_problem(JELLYFIN_URL)


def jellyfin_auth_header():
    return jellyfin_api.auth_header(JELLYFIN_API_KEY)


async def jf_get(path, params=None):
    """One authenticated Jellyfin GET returning parsed JSON; a rejected key raises `JellyfinAuthError`."""
    return await jellyfin_api.service(JELLYFIN_URL, JELLYFIN_API_KEY, user_agent=USER_AGENT).get(path, params)


async def search_tracks(query, limit=MAX_SEARCH_RESULTS):
    params = {"searchTerm": query, "recursive": "true", "includeItemTypes": "Audio", "fields": AUDIO_FIELDS, "limit": limit}
    return (await jf_get("/Items", params)).get("Items", [])


def track_from_item(item):
    """`Static=true` streams the original file untouched; ffmpeg decodes whatever container it is."""
    artist = item.get("AlbumArtist") or ", ".join(item.get("Artists") or [])
    name = item.get("Name") or "Unknown track"
    return Track(
        title=f"{artist} - {name}" if artist else name,
        url=f"{JELLYFIN_URL}/Audio/{item['Id']}/stream?Static=true",
        duration_seconds=(item.get("RunTimeTicks") or 0) / 10_000_000,
        headers=f"Authorization: {jellyfin_auth_header()}\r\n",
    )


def is_stream_url(text):
    text = text.strip()
    parts = urllib.parse.urlsplit(text)
    return parts.scheme in ("http", "https") and bool(parts.hostname) and " " not in text


class StreamRefused(Exception):
    """A direct stream URL that cannot be played; `str(self)` is the sentence to reply with."""


def _is_playlist(answer):
    kind = answer.headers.get("content-type", "").lower()
    path = urllib.parse.urlsplit(answer.url).path.lower()
    head = answer.body.lstrip(b"\xef\xbb\xbf \r\n\t").lower()
    return any(t in kind for t in PLAYLIST_TYPES) or path.endswith(PLAYLIST_SUFFIXES) or head.startswith(PLAYLIST_HEADS)


async def open_stream(url):
    """The track for a direct stream URL after following and vetting every redirect, so ffmpeg is handed the address that answered.
    A private or unresolvable host, a playlist or a stream that does not answer raises `StreamRefused`; the configured Jellyfin host is exempt."""
    allowed = [host_of(JELLYFIN_URL)] if JELLYFIN_URL else []
    service = JsonService(
        "stream", user_agent=USER_AGENT, timeout=STREAM_TIMEOUT_SECONDS, total_timeout=STREAM_DEADLINE_SECONDS,
        public_only=True, allow_hosts=allowed, cross_origin_redirects=True, transport=TRANSPORT,
    )
    try:
        answer = await service.peek(url, size=STREAM_PEEK_BYTES)
    except ServiceBlocked as err:
        reason = "that address is on a private network - only public stream URLs are allowed."
        raise StreamRefused(reason if err.reason == PRIVATE else "could not resolve that host.") from None
    except ServiceError:
        raise StreamRefused("could not open that stream.") from None
    if not 200 <= answer.status < 300:
        raise StreamRefused(f"that stream answered HTTP {answer.status}.")
    if _is_playlist(answer):
        raise StreamRefused("that url is a playlist - give me the direct stream url inside it.")
    track = track_from_url(url)
    track.url = answer.url
    return track


def track_from_url(url):
    parts = urllib.parse.urlsplit(url)
    last_segment = urllib.parse.unquote(parts.path).rstrip("/").rsplit("/", 1)[-1]
    return Track(title=last_segment or parts.hostname or url, url=url)
