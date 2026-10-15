"""Turns a pasted URL into the feed URL behind it, and fetches it conditionally with a timeout, a size cap and a host guard."""

import re
import urllib.parse
from dataclasses import dataclass

from slimbots.web import JsonService, ServiceBlocked, ServiceTooLarge

import parse as parsemod

MAX_BODY = 2 * 1024 * 1024
TIMEOUT = 20
USER_AGENT = "slimm-bot-feeds/1.0"
ACCEPT = "application/atom+xml, application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.1"
YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id="
FEED_SUFFIXES = (".rss", ".atom", ".xml")


class SourceError(Exception):
    """A URL we will not fetch; the message is safe to show a member."""


def normalize(url):
    """The feed URL for a YouTube channel, Reddit, Mastodon account or Forgejo/Gitea/GitHub releases page; anything else passes through."""
    parts = urllib.parse.urlsplit(url.strip())
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise SourceError("that is not an http or https URL")
    host, path = parts.hostname.lower().removeprefix("www."), parts.path.rstrip("/")
    if path.endswith(FEED_SUFFIXES) or parts.query and "channel_id=" in parts.query:
        return parts.geturl()
    if host in ("youtube.com", "m.youtube.com"):
        match = re.fullmatch(r"/channel/(UC[\w-]{20,})", path)
        if not match:
            raise SourceError("for YouTube use the channel page with its id (youtube.com/channel/UC...); a @handle URL does not carry it")
        return YOUTUBE_FEED + match.group(1)
    if host in ("reddit.com", "old.reddit.com") and re.match(r"/(r|u|user)/[\w-]+", path):
        return urllib.parse.urlunsplit(("https", "www.reddit.com", path + "/.rss", parts.query, ""))
    if re.fullmatch(r"/@[\w.-]+", path):
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path + ".rss", "", ""))
    if re.fullmatch(r"/[^/]+/[^/]+/releases", path):
        suffix = ".atom" if host == "github.com" else ".rss"
        return urllib.parse.urlunsplit((parts.scheme, parts.netloc, path + suffix, "", ""))
    return parts.geturl()


async def web_transport(url, headers, allow_private=False, transport=None):
    """One GET returning (status, lowercase headers, body); a non-2xx answer is a result, a refused host or a body over MAX_BODY a SourceError.
    Every redirect hop is checked too, so a public url cannot bounce into the LAN."""
    service = JsonService(
        "feed", user_agent=None, timeout=TIMEOUT, max_bytes=MAX_BODY, public_only=True, allow_private=allow_private,
        cross_origin_redirects=True, transport=transport,
    )
    try:
        response = await service.send("GET", url, headers=headers)
    except ServiceBlocked:
        raise SourceError("that host is not publicly routable (set FEEDS_ALLOW_PRIVATE=1 to allow a LAN feed)") from None
    except ServiceTooLarge:
        raise SourceError("the feed is larger than 2 MB") from None
    return response.status, response.headers, response.body


@dataclass
class Result:
    status: str
    parsed: parsemod.Parsed | None = None
    etag: str | None = None
    last_modified: str | None = None
    detail: str = ""


async def fetch(url, etag=None, last_modified=None, transport=web_transport):
    """Never raises: every failure is an `error` result whose detail is safe to store and show."""
    headers = {"user-agent": USER_AGENT, "accept": ACCEPT}
    if etag:
        headers["if-none-match"] = etag
    if last_modified:
        headers["if-modified-since"] = last_modified
    try:
        status, got, body = await transport(url, headers)
    except SourceError as err:
        return Result("error", detail=str(err))
    except Exception as err:
        return Result("error", detail=f"could not connect ({type(err).__name__})")
    if status == 304:
        return Result("unchanged")
    if status != 200:
        return Result("error", detail=f"http {status}")
    if len(body) > MAX_BODY:
        return Result("error", detail="the feed is larger than 2 MB")
    try:
        parsed = parsemod.parse(body)
    except parsemod.FeedError as err:
        return Result("error", detail=str(err))
    return Result("ok", parsed=parsed, etag=got.get("etag"), last_modified=got.get("last-modified"))
