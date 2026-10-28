"""The daily deals digest: Steam's featured specials, trimmed to what is new and short enough to read."""

import re
from dataclasses import dataclass

import prices
import sources

SPECIALS_URL = "https://store.steampowered.com/api/featuredcategories?cc={cc}&l=en"
MAX_GAMES = 10
MAX_TEXT = 1800
REPEAT_AFTER_DAYS = 7


@dataclass(frozen=True)
class Deal:
    app_id: int
    name: str
    percent: int
    cents: int
    original_cents: int
    currency: str


def parse_specials(data):
    """Discounted apps from the featured specials; entries that are not priced discounts are skipped, a changed shape is a SourceError."""
    section = data.get("specials") if isinstance(data, dict) else None
    items = section.get("items") if isinstance(section, dict) else None
    if not isinstance(items, list):
        raise sources.SourceError("unexpected Steam specials answer")
    deals = []
    for item in items:
        deal = _deal(item)
        if deal:
            deals.append(deal)
    return sorted(deals, key=lambda d: -d.percent)


def _deal(item):
    if not isinstance(item, dict) or not item.get("discounted"):
        return None
    numbers = [item.get(key) for key in ("id", "discount_percent", "final_price", "original_price")]
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in numbers) or not isinstance(item.get("name"), str):
        return None
    if not 0 < item["discount_percent"] <= 100 or item["final_price"] > item["original_price"]:
        return None
    return Deal(item["id"], item["name"], item["discount_percent"], item["final_price"], item["original_price"], str(item.get("currency") or ""))


def fetch_specials(transport, cc):
    """Returns (deals, error); a failed fetch is a message safe to show, never an exception."""
    try:
        return parse_specials(sources.get_json(transport, SPECIALS_URL.format(cc=cc))), ""
    except sources.SourceError as err:
        return [], str(err)


def link_text(name):
    """A store name made safe inside a markdown link: no brackets, no mentions."""
    return re.sub(r"[\[\]]", "", name).replace("@", " at ").strip() or "game"


def line(deal, link):
    now = prices.format_cents(deal.cents, deal.currency)
    was = prices.format_cents(deal.original_cents, deal.currency)
    return f"[{link_text(deal.name)}]({link}) - {deal.percent}% off, {now} (was {was})"


def render(deals, store_link):
    """One line per game, as many as fit the length cap; returns (description, games shown)."""
    lines, size = [], 0
    for deal in deals[:MAX_GAMES]:
        text = line(deal, store_link(deal.app_id))
        if size + len(text) + 1 > MAX_TEXT:
            break
        lines.append(text)
        size += len(text) + 1
    return "\n".join(lines), len(lines)
