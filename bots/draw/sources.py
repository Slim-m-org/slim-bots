"""Picture sources (Google, Bing, Wikipedia, Commons) and the decode step; the fetch is injectable so tests need no network."""

import html as html_module
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
BING_IMAGES = "https://www.bing.com/images/async"
GOOGLE_CSE = "https://www.googleapis.com/customsearch/v1"
SERPAPI = "https://serpapi.com/search.json"
WIKI_PAGES = 3
COMMONS_FILES = 6
WEB_RESULTS = 5
PICTURE_TYPES = ("image/jpeg", "image/png", "image/webp")
FILLER = {"a", "an", "the", "of", "portrait", "picture", "photo", "photograph", "drawing", "image", "realistic", "hyperrealistic", "me", "please", "draw"}
BING_ENTRY = re.compile(r'\bm="(\{.*?\})"', re.S)


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


def bing_candidates(get, subject, report=None):
    """Bing image results, best first: the full-size picture URL (`murl`), its title and the page it was on (`purl`), read from the async results HTML."""
    params = {"q": subject, "first": 0, "count": WEB_RESULTS * 2, "mmasync": 1}
    status, page = get(f"{BING_IMAGES}?{urllib.parse.urlencode(params)}")
    if status != 200:
        _say(report, f"bing: http {status}")
        return []
    found = []
    for raw in BING_ENTRY.findall(page.decode("utf-8", "replace")):
        try:
            entry = json.loads(html_module.unescape(raw))
        except ValueError:
            continue
        if isinstance(entry, dict) and str(entry.get("murl", "")).startswith("http"):
            found.append(_candidate(entry["murl"], html_module.unescape(str(entry.get("t", ""))), entry.get("purl"), "bing"))
        if len(found) >= WEB_RESULTS:
            break
    if not found:
        _say(report, "bing: no results in the answer")
    return found


def google_candidates(get, subject, key, engine, report=None):
    """Image results from Google's official Programmable Search; the key goes in the request but never into a message."""
    data = _json(get, GOOGLE_CSE, {"key": key, "cx": engine, "q": subject, "searchType": "image", "num": WEB_RESULTS, "safe": "off"}, "google search")
    return [
        _candidate(i["link"], i.get("title"), (i.get("image") or {}).get("contextLink"), "google")
        for i in data.get("items", []) if i.get("link")
    ]


def _masked(text, key):
    return str(text).replace(key, "***") if key else str(text)


def serpapi_candidates(get, subject, key, report=None, on_call=None):
    """Google Images results through SerpAPI (`images_results`): the original picture, its page (`link`) and the thumbnail as a fallback; `on_call` hears each request, the key is masked in every message."""
    if on_call:
        on_call()
    status, body = get(f"{SERPAPI}?{urllib.parse.urlencode({'engine': 'google_images', 'q': subject, 'safe': 'off', 'api_key': key})}")
    try:
        data = json.loads(body)
    except (ValueError, TypeError):
        data = None
    if status != 200 or not isinstance(data, dict) or data.get("error"):
        detail = _masked(data.get("error", ""), key)[:60] if isinstance(data, dict) else ""
        raise SourceError(f"serpapi answered http {status}{': ' + detail if detail else ''}")
    found = []
    for item in data.get("images_results") or []:
        original = item.get("original")
        if not str(original or "").startswith("http"):
            continue
        entry = _candidate(original, item.get("title") or item.get("source"), item.get("link"), "serpapi")
        if str(item.get("thumbnail", "")).startswith("http"):
            entry["fallback"] = item["thumbnail"]
        found.append(entry)
        if len(found) >= WEB_RESULTS:
            break
    if not found:
        _say(report, "serpapi: no images in the answer")
    return found


def find_pictures(get, subject, google=None, report=None, serpapi=None, on_call=None):
    """Candidates for `subject`, read lazily one source at a time: SerpAPI (if `serpapi` is a key), Google CSE (if `google` is a (key, engine) pair), Bing, Wikipedia's lead image, Commons; a failing source is skipped and reported."""
    steps = [("serpapi", lambda: serpapi_candidates(get, subject, serpapi, report, on_call))] if serpapi else []
    steps += [("google", lambda: google_candidates(get, subject, *google, report=report))] if google else []
    steps += [
        ("bing", lambda: bing_candidates(get, subject, report)),
        ("wikipedia", lambda: [c] if (c := wikipedia_candidate(get, subject, report)) else []),
        ("commons", lambda: commons_candidates(get, subject, report)),
    ]
    for name, step in steps:
        try:
            yield from step()
        except SourceError as err:
            _say(report, f"{name}: {_masked(err, serpapi)}")


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
