"""Openly licensed pictures from Openverse and the decode step; both are injectable so tests need no network."""

import html as html_module
import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request

SEARCH_URL = "https://api.openverse.org/v1/images/"
TIMEOUT = 15
MAX_PICTURE_BYTES = 3 * 1024 * 1024
MAX_PIXELS = 25_000_000
USER_AGENT = "slimm-bot-draw/1.0 (https://github.com/Slim-m-org/slim-bots; a slim-m community drawing bot)"


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


def reference_image(data, side=768):
    """(JPEG bytes, media type) of a downloaded picture shrunk to at most `side` pixels, for a model to look at."""
    try:
        from PIL import Image
    except ImportError as err:
        raise SourceError("Pillow is not installed on this bot, so it cannot read pictures") from err
    Image.MAX_IMAGE_PIXELS = MAX_PIXELS
    try:
        with Image.open(io.BytesIO(data)) as picture:
            picture.draft("RGB", (side * 2, side * 2))
            rgb = picture.convert("RGB")
            rgb.thumbnail((side, side))
            out = io.BytesIO()
            rgb.save(out, "JPEG", quality=82)
    except Exception as err:
        raise SourceError("the picture could not be read") from err
    return out.getvalue(), "image/jpeg"


WIKI_API = "https://en.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
WIKI_PAGES = 3
FILLER = {"a", "an", "the", "of", "portrait", "picture", "photo", "photograph", "drawing", "image", "realistic", "hyperrealistic", "me", "please", "draw"}
FREE_LICENCE = re.compile(r"^(cc[ -]|public domain|pd[ -]|pd$|cc0)", re.I)


def clean_subject(subject):
    """The subject without drawing words ("portrait", "hyperrealistic"), which only confuse an encyclopedia search."""
    words = [w for w in re.split(r"\s+", subject.strip()) if w.lower() not in FILLER]
    return " ".join(words) or subject.strip()


def _json(get, base, params):
    status, body = get(f"{base}?{urllib.parse.urlencode(params)}")
    if status != 200:
        raise SourceError(f"wikipedia answered http {status}")
    try:
        return json.loads(body)
    except (ValueError, AttributeError) as err:
        raise SourceError("wikipedia gave an answer I could not read") from err


def _plain(html):
    return " ".join(html_module.unescape(re.sub(r"<[^>]*>", " ", html or "")).split())[:60]


def _free_file(get, filename):
    """(licence, author) of a Commons file when it is freely licensed, else None; a file Commons lacks is non-free."""
    data = _json(get, COMMONS_API, {
        "action": "query", "format": "json", "titles": f"File:{filename}", "prop": "imageinfo",
        "iiprop": "extmetadata", "iiextmetadatafilter": "LicenseShortName|Artist|NonFree",
    })
    for page in (data.get("query", {}).get("pages") or {}).values():
        meta = ((page.get("imageinfo") or [{}])[0]).get("extmetadata") or {}
        licence = (meta.get("LicenseShortName") or {}).get("value", "")
        nonfree = str((meta.get("NonFree") or {}).get("value", "")).lower() in ("true", "1", "yes")
        if licence and not nonfree and FREE_LICENCE.match(licence):
            return licence[:40], _plain((meta.get("Artist") or {}).get("value")) or "unknown"
    return None


def wikipedia_candidate(get, subject):
    """The lead image of the best Wikipedia article for `subject` as a candidate dict, if its file is freely licensed; else None."""
    data = _json(get, WIKI_API, {
        "action": "query", "format": "json", "generator": "search", "gsrsearch": clean_subject(subject), "gsrlimit": WIKI_PAGES,
        "prop": "pageimages|info", "piprop": "thumbnail", "pithumbsize": 800, "inprop": "url",
    })
    pages = sorted((data.get("query", {}).get("pages") or {}).values(), key=lambda page: page.get("index", 99))
    for page in pages:
        thumbnail, filename = (page.get("thumbnail") or {}).get("source"), page.get("pageimage")
        if not thumbnail or not filename:
            continue
        free = _free_file(get, filename)
        if free:
            return {"url": thumbnail, "title": page.get("title", "untitled")[:80], "creator": free[1], "license": free[0], "landing": page.get("fullurl", "")}
    return None


def find_pictures(get, subject, openverse_get=None):
    """Candidates for `subject`: the free Wikipedia lead image, else Openverse; `get` has no credentials, `openverse_get` may, and its token never reaches Wikipedia."""
    try:
        wiki = wikipedia_candidate(get, subject)
    except SourceError:
        wiki = None
    if wiki:
        return [{**wiki, "source": "wikipedia"}]
    return [{**c, "source": "openverse"} for c in search(openverse_get or get, subject)]
