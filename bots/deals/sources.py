"""Free-to-keep Steam offers from GamerPower, fetched through an injectable transport."""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

TIMEOUT = 20
MAX_BODY = 2 * 1024 * 1024
USER_AGENT = "slimm-bot-deals/1.0"
GAMERPOWER_URL = "https://www.gamerpower.com/api/giveaways?platform=steam&type=game"
STORE = "steam"
STORE_NAME = "Steam"


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


def parse_gamerpower(data):
    if isinstance(data, dict):
        return []
    if not isinstance(data, list):
        raise SourceError("unexpected GamerPower answer")
    offers = []
    for item in data:
        if item.get("type") != "Game" or item.get("status") != "Active" or not item.get("open_giveaway_url"):
            continue
        offers.append(Offer(
            STORE, str(item["id"]), item["title"], item["open_giveaway_url"], parse_time(item.get("end_date")),
            item.get("worth") if item.get("worth") != "N/A" else "", item.get("image") or "",
        ))
    return offers


def fetch_offers(transport=urllib_transport):
    """Returns (offers, error); a failed fetch is a message safe to show, never an exception."""
    try:
        return parse_gamerpower(get_json(transport, GAMERPOWER_URL)), ""
    except SourceError as err:
        return [], str(err)
