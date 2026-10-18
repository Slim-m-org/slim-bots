"""Steam store prices: link and price parsing, the appdetails lookup, and the rule for when a watcher is pinged."""

import json
import re
import urllib.parse
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

import sources

APPDETAILS_URL = "https://store.steampowered.com/api/appdetails?appids={app_id}&cc={cc}&filters=basic,price_overview"
APP_LINK = re.compile(r"/app/(\d{1,10})(?:/|$)")
MAX_PRICE_CENTS = 1_000_000


class PriceError(Exception):
    """A bad member input; the message is safe to show."""


@dataclass(frozen=True)
class Quote:
    """status is ok, free, unavailable (not sold in this region, or removed) or error."""

    status: str
    name: str = ""
    cents: int | None = None
    currency: str = ""
    discount_percent: int = 0
    detail: str = ""


def parse_app_id(text):
    """A bare app id or a store.steampowered.com/app/<id> link; anything else is a PriceError."""
    text = text.strip().strip("<>")
    if text.isdigit() and 0 < int(text) < 10**10:
        return int(text)
    parts = urllib.parse.urlsplit(text if "//" in text else f"https://{text}")
    match = APP_LINK.search(parts.path)
    if match and (parts.hostname or "") in ("store.steampowered.com", "steamcommunity.com"):
        return int(match.group(1))
    raise PriceError("give me a Steam store link (store.steampowered.com/app/...) or an app id")


def parse_price_cents(text):
    """`4.99`, `$4.99`, `4,99` or `5` into cents, in the store's minor unit; zero, negative and absurd values are refused."""
    if text.strip().startswith("-"):
        raise PriceError("the target price must be above zero and a believable amount")
    cleaned = re.sub(r"[^\d.,]", "", text.strip())
    if cleaned.count(",") == 1 and "." not in cleaned and len(cleaned.split(",")[1]) <= 2:
        cleaned = cleaned.replace(",", ".")
    try:
        cents = int((Decimal(cleaned.replace(",", "")) * 100).to_integral_value())
    except (InvalidOperation, ValueError) as err:
        raise PriceError("give me a target price like 4.99") from err
    if not 0 < cents <= MAX_PRICE_CENTS:
        raise PriceError("the target price must be above zero and a believable amount")
    return cents


def format_cents(cents, currency):
    return f"{cents // 100}.{cents % 100:02d} {currency}"


def fetch_quote(transport, app_id, cc):
    """Never raises: a failure is an `error` quote, a missing or removed app an `unavailable` one."""
    try:
        data = sources.get_json(transport, APPDETAILS_URL.format(app_id=app_id, cc=cc))
    except sources.SourceError as err:
        return Quote("error", detail=str(err))
    entry = data.get(str(app_id)) if isinstance(data, dict) else None
    if not isinstance(entry, dict):
        return Quote("error", detail="unexpected Steam answer")
    if not entry.get("success") or not entry.get("data"):
        return Quote("unavailable")
    info = entry["data"]
    name = info.get("name") or f"app {app_id}"
    overview = info.get("price_overview")
    if not overview:
        return Quote("free", name=name) if info.get("is_free") else Quote("unavailable", name=name)
    final = overview.get("final")
    if not isinstance(final, int) or isinstance(final, bool):
        return Quote("error", detail="unexpected Steam price")
    return Quote("ok", name, final, str(overview.get("currency") or ""), int(overview.get("discount_percent") or 0))


def should_notify(price_cents, target_cents, notified_cents):
    """Ping at or below target, then again only on a further drop; a price back above target re-arms it."""
    if price_cents > target_cents:
        return False
    return notified_cents is None or price_cents < notified_cents
