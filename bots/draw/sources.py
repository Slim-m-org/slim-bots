"""Openly licensed pictures from Openverse and the decode step; both are injectable so tests need no network."""

import io
import json
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://api.openverse.org/v1/images/"
TIMEOUT = 15
MAX_PICTURE_BYTES = 3 * 1024 * 1024
MAX_PIXELS = 25_000_000
USER_AGENT = "slimm-bot-draw/1.0"


class SourceError(Exception):
    """A search, download or decode failure whose message is safe to show; it never carries a token or a URL."""


def http_get(url, token=""):
    """One GET returning (status, body), capped at MAX_PICTURE_BYTES; a connection failure is a SourceError."""
    headers = {"user-agent": USER_AGENT, **({"authorization": f"Bearer {token}"} if token else {})}
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=TIMEOUT) as response:
            body = response.read(MAX_PICTURE_BYTES + 1)
            return response.status, body
    except urllib.error.HTTPError as err:
        return err.code, b""
    except Exception as err:
        raise SourceError(f"could not connect ({type(err).__name__})") from err


def search(get, query, limit=8):
    """Candidates with a licence and a thumbnail: {url, title, creator, license, landing}, mature results excluded."""
    params = {"q": query, "page_size": limit, "mature": "false", "category": "illustration,digitized_artwork,photograph", "license_type": "commercial"}
    status, body = get(f"{SEARCH_URL}?{urllib.parse.urlencode(params)}")
    if status == 429:
        raise SourceError("the picture search is rate limited right now; try again in a minute")
    if status != 200:
        raise SourceError(f"the picture search answered http {status}")
    try:
        results = json.loads(body).get("results", [])
    except (ValueError, AttributeError) as err:
        raise SourceError("the picture search gave an answer I could not read") from err
    found = []
    for item in results:
        if item.get("thumbnail") and item.get("license"):
            version = f" {item['license_version']}" if item.get("license_version") else ""
            found.append({
                "url": item["thumbnail"], "title": (item.get("title") or "untitled")[:80], "creator": (item.get("creator") or "unknown")[:60],
                "license": f"CC {item['license'].upper()}{version}", "landing": item.get("foreign_landing_url") or item.get("url") or "",
            })
    return found


def fetch_picture(get, url):
    status, body = get(url)
    if status != 200 or not body:
        raise SourceError(f"the picture could not be downloaded (http {status})")
    if len(body) > MAX_PICTURE_BYTES:
        raise SourceError("the picture is too large")
    return body


def decode_gray(data, size):
    """(grid of 0-255 rows, width, height) fitted inside size x size, using Pillow when it is installed."""
    try:
        from PIL import Image
    except ImportError as err:
        raise SourceError("Pillow is not installed on this bot, so it cannot read pictures") from err
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as picture:
            picture.draft("L", (size * 2, size * 2))
            gray = picture.convert("L")
            gray.thumbnail((size, size))
            width, height = gray.size
            pixels = list(gray.getdata())
    except Exception as err:
        raise SourceError("the picture could not be read") from err
    return [pixels[y * width : (y + 1) * width] for y in range(height)], width, height
