"""bot-music's settings, track model, Jellyfin audio search and stream-URL vetting; see README.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" in docs/framework.md."""

import urllib.parse
from dataclasses import dataclass

from slimbots import jellyfin as jellyfin_api
from slimbots.jellyfin import JellyfinAuthError  # noqa: F401 - re-exported for the cog
from slimbots.urlpolicy import PRIVATE, destination_problem, host_of

MAX_QUERY_LENGTH = 200
MAX_QUEUE_LENGTH = 50
MAX_SEARCH_RESULTS = 5
USER_AGENT = "slimm-bot-music/1.0"
AUDIO_FIELDS = "Album,AlbumArtist,Artists,RunTimeTicks"

# Populated once by configure(); empty JELLYFIN_URL means direct URLs only.
JELLYFIN_URL = ""
JELLYFIN_API_KEY = ""


@dataclass
class Track:
    title: str
    url: str
    duration_seconds: float = 0.0
    headers: str | None = None


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


async def check_stream_url(url):
    """Refuses a URL whose host resolves to a private address, so `~play` cannot probe the bot host's LAN.
    The configured Jellyfin host is exempt; a resolver that changes its answer between this check and ffmpeg's fetch is not caught."""
    allowed = [host_of(JELLYFIN_URL)] if JELLYFIN_URL else []
    problem = await destination_problem(url, allow_hosts=allowed)
    if problem is None:
        return None
    if problem == PRIVATE:
        return "that address is on a private network - only public stream URLs are allowed."
    return "could not resolve that host."


def track_from_url(url):
    parts = urllib.parse.urlsplit(url)
    last_segment = urllib.parse.unquote(parts.path).rstrip("/").rsplit("/", 1)[-1]
    return Track(title=last_segment or parts.hostname or url, url=url)
