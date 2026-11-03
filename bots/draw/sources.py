"""Openly licensed pictures from Openverse and the decode step; both are injectable so tests need no network."""

import io
import json
import re
import urllib.error
import urllib.parse
import urllib.request

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
DDG_PAGE = "https://duckduckgo.com/"
DDG_IMAGES = "https://duckduckgo.com/i.js"
GOOGLE_CSE = "https://www.googleapis.com/customsearch/v1"
WIKI_PAGES = 3
COMMONS_FILES = 6
WEB_RESULTS = 6
PICTURE_TYPES = ("image/jpeg", "image/png", "image/webp")
FILLER = {"a", "an", "the", "of", "portrait", "picture", "photo", "photograph", "drawing", "image", "realistic", "hyperrealistic", "me", "please", "draw"}
VQD = re.compile(rb"vqd=[\"']?([\d-]+)")


def clean_subject(subject):
    """The subject without drawing words ("portrait", "hyperrealistic"), which only confuse a search."""
    words = [w for w in re.split(r"\s+", subject.strip()) if w.lower() not in FILLER]
    return " ".join(words) or subject.strip()


def _say(report, text):
    if report is not None:
        report(text[:100])


def _json(get, base, params, what):
    status, body = get(f"{base}?{urllib.parse.urlencode(params)}")
    if status != 200:
        raise SourceError(f"{what} answered http {status}")
    try:
        return json.loads(body)
    except (ValueError, AttributeError) as err:
        raise SourceError(f"{what} gave an answer I could not read") from err


def _candidate(url, title, landing, source, creator=""):
    return {"url": url, "title": (title or "untitled")[:80], "creator": (creator or "")[:60], "license": "", "landing": landing or "", "source": source}


def wikipedia_candidate(get, subject, report=None):
    """The lead image of the best Wikipedia article for `subject` as a candidate dict, or None."""
    data = _json(get, WIKI_API, {
        "action": "query", "format": "json", "generator": "search", "gsrsearch": clean_subject(subject), "gsrlimit": WIKI_PAGES,
        "prop": "pageimages|info", "piprop": "thumbnail|name", "pithumbsize": 800, "inprop": "url",
    }, "wikipedia")
    pages = sorted((data.get("query", {}).get("pages") or {}).values(), key=lambda page: page.get("index", 99))
    for page in pages:
        thumbnail = (page.get("thumbnail") or {}).get("source")
        if thumbnail:
            return _candidate(thumbnail, page.get("title"), page.get("fullurl"), "wikipedia")
        _say(report, f"{page.get('title', '?')}: no lead image")
    return None


def commons_candidates(get, subject, report=None):
    """Picture files from a Wikimedia Commons file search (namespace 6), best match first."""
    data = _json(get, COMMONS_API, {
        "action": "query", "format": "json", "generator": "search", "gsrnamespace": 6, "gsrsearch": clean_subject(subject), "gsrlimit": COMMONS_FILES,
        "prop": "imageinfo", "iiprop": "url|mime|size", "iiurlwidth": 800,
    }, "commons")
    pages = sorted((data.get("query", {}).get("pages") or {}).values(), key=lambda page: page.get("index", 99))
    found = []
    for page in pages:
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") in PICTURE_TYPES and info.get("thumburl") and info.get("width", 0) >= 300:
            found.append(_candidate(info["thumburl"], str(page.get("title", "")).removeprefix("File:"), info.get("descriptionurl"), "commons"))
    if not found:
        _say(report, "commons: no picture file matched")
    return found


def duckduckgo_candidates(get, subject, report=None):
    """DuckDuckGo image results through its unofficial endpoint and `vqd` token; it often answers with a bot check, which is a quiet empty result."""
    query = {"q": subject, "iax": "images", "ia": "images"}
    status, page = get(f"{DDG_PAGE}?{urllib.parse.urlencode(query)}")
    token = VQD.search(page) if status == 200 else None
    if not token:
        _say(report, f"duckduckgo: no token (http {status})")
        return []
    params = {"l": "us-en", "o": "json", "q": subject, "vqd": token.group(1).decode(), "f": ",,,,,", "p": "1"}
    status, body = get(f"{DDG_IMAGES}?{urllib.parse.urlencode(params)}")
    try:
        results = json.loads(body).get("results", []) if status == 200 else []
    except (ValueError, AttributeError):
        results = []
    found = [_candidate(r["image"], r.get("title"), r.get("url"), "duckduckgo") for r in results[:WEB_RESULTS] if r.get("image")]
    if not found:
        _say(report, f"duckduckgo: no results (http {status})")
    return found


def google_candidates(get, subject, key, engine, report=None):
    """Image results from Google's official Programmable Search; the key goes in the request but never into a message."""
    data = _json(get, GOOGLE_CSE, {"key": key, "cx": engine, "q": subject, "searchType": "image", "num": WEB_RESULTS, "safe": "off"}, "google search")
    return [
        _candidate(i["link"], i.get("title"), (i.get("image") or {}).get("contextLink"), "google")
        for i in data.get("items", []) if i.get("link")
    ]


def find_pictures(get, subject, google=None, report=None):
    """Candidates for `subject`, read lazily one source at a time: Google (if `google` is a (key, engine) pair), DuckDuckGo, the Wikipedia lead image, Commons; a failing source is skipped and reported."""
    steps = [("google", lambda: google_candidates(get, subject, *google, report=report))] if google else []
    steps += [
        ("duckduckgo", lambda: duckduckgo_candidates(get, subject, report)),
        ("wikipedia", lambda: [c] if (c := wikipedia_candidate(get, subject, report)) else []),
        ("commons", lambda: commons_candidates(get, subject, report)),
    ]
    for name, step in steps:
        try:
            yield from step()
        except SourceError as err:
            _say(report, f"{name}: {err}")


def credit_line(candidate):
    """`"Title" by Creator (Licence) page`, leaving out what the source did not say."""
    parts = [f'"{candidate["title"]}"']
    if candidate.get("creator") and candidate["creator"] != "unknown":
        parts.append(f"by {candidate['creator']}")
    if candidate.get("license"):
        parts.append(f"({candidate['license']})")
    if candidate.get("landing"):
        parts.append(candidate["landing"])
    return " ".join(parts)
