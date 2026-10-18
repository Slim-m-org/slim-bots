"""Free-to-keep offers from Epic's own endpoint and GamerPower (Steam, GOG), fetched through an injectable transport."""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

TIMEOUT = 20
MAX_BODY = 2 * 1024 * 1024
USER_AGENT = "slimm-bot-deals/1.0"
EPIC_URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=en-US&country={country}&allowCountries={country}"
EPIC_STORE = "https://store.epicgames.com/en-US/p/"
GAMERPOWER_URL = "https://www.gamerpower.com/api/giveaways?platform={platform}&type=game"
GAMERPOWER_PLATFORMS = {"steam": "steam", "gog": "gog"}
ALL_SOURCES = ("epic", "steam", "gog")
STORE_NAMES = {"epic": "Epic Games Store", "steam": "Steam", "gog": "GOG"}


class SourceError(Exception):
    """A fetch or parse failure whose message is safe to store and show."""


@dataclass(frozen=True)
class Offer:
    store: str
    offer_id: str
    title: str
    url: str
    ends_at: datetime | None = None
    worth: str = ""
    image: str = ""


def urllib_transport(url, headers):
    """One GET returning (status, body); a non-2xx answer is a result, an oversized body a SourceError."""
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            body = response.read(MAX_BODY + 1)
            status = response.status
    except urllib.error.HTTPError as err:
        return err.code, b""
    if len(body) > MAX_BODY:
        raise SourceError("the answer is larger than 2 MB")
    return status, body


def get_json(transport, url):
    try:
        status, body = transport(url, {"user-agent": USER_AGENT, "accept": "application/json"})
    except SourceError:
        raise
    except Exception as err:
        raise SourceError(f"could not connect ({type(err).__name__})") from err
    if status != 200 and not (status == 201 and body):
        raise SourceError(f"http {status}")
    try:
        return json.loads(body)
    except ValueError as err:
        raise SourceError("the answer is not JSON") from err


def parse_time(text):
    """ISO or `YYYY-MM-DD HH:MM:SS` (read as UTC); None for anything else, including GamerPower's `N/A`."""
    if not isinstance(text, str):
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d %H:%M:%S"):
        try:
            parsed = datetime.strptime(text, fmt)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def epic_slug(element):
    for mapping in (element.get("offerMappings") or []) + ((element.get("catalogNs") or {}).get("mappings") or []):
        if mapping.get("pageSlug"):
            return mapping["pageSlug"]
    return element.get("productSlug") or element.get("urlSlug") or ""


def epic_window(element):
    """The live 100%-off window as (start, end), or None; Epic encodes a free game as discountPercentage 0 on a zero price."""
    price = (element.get("price") or {}).get("totalPrice") or {}
    if price.get("discountPrice") != 0 or not price.get("originalPrice"):
        return None
    for group in ((element.get("promotions") or {}).get("promotionalOffers") or []):
        for window in group.get("promotionalOffers") or []:
            return parse_time(window.get("startDate")), parse_time(window.get("endDate"))
    return None


def parse_epic(data):
    try:
        elements = data["data"]["Catalog"]["searchStore"]["elements"]
    except (KeyError, TypeError) as err:
        raise SourceError("unexpected Epic answer") from err
    offers = []
    for element in elements:
        window = epic_window(element)
        slug = epic_slug(element)
        if window is None or not slug or element.get("offerType") not in ("BASE_GAME", "BUNDLE", "EDITION"):
            continue
        offers.append(Offer("epic", element["id"], element["title"], EPIC_STORE + slug, window[1]))
    return offers


def parse_gamerpower(data, store):
    if isinstance(data, dict):
        return []
    if not isinstance(data, list):
        raise SourceError("unexpected GamerPower answer")
    offers = []
    for item in data:
        if item.get("type") != "Game" or item.get("status") != "Active" or not item.get("open_giveaway_url"):
            continue
        offers.append(Offer(
            store, str(item["id"]), item["title"], item["open_giveaway_url"], parse_time(item.get("end_date")),
            item.get("worth") if item.get("worth") != "N/A" else "", item.get("image") or "",
        ))
    return offers


def fetch_offers(sources, transport=urllib_transport, country="US"):
    """Returns (offers, errors) over the chosen sources; one failing source never hides the others."""
    offers, errors = [], {}
    for name in sources:
        try:
            if name == "epic":
                offers += parse_epic(get_json(transport, EPIC_URL.format(country=country)))
            else:
                offers += parse_gamerpower(get_json(transport, GAMERPOWER_URL.format(platform=GAMERPOWER_PLATFORMS[name])), name)
        except SourceError as err:
            errors[name] = str(err)
    return offers, errors
