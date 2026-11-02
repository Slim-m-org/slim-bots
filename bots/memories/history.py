"""Reading a window of channel history, the quote picker and the monthly numbers; no member ever gets a score."""

import calendar
import hashlib
from collections import Counter
from datetime import datetime, timedelta, timezone

PAGE = 100
MIN_QUOTE = 20
MAX_QUOTE = 280


async def fetch_window(client, channel_id, start_ms, end_ms, max_pages):
    """Messages with start_ms <= created_at < end_ms, paging back from the newest; stops at max_pages."""
    found, before = [], None
    for _ in range(max_pages):
        params = {"limit": PAGE, **({"before": before} if before else {})}
        page = await client.call("GET", f"/channels/{channel_id}/messages", params=params)
        if not page:
            break
        found.extend(m for m in page if start_ms <= m["created_at"] < end_ms)
        before = min(m["seq"] for m in page)
        if len(page) < PAGE or min(m["created_at"] for m in page) < start_ms:
            break
    return found


def months_before(day, months):
    """The same day-of-month `months` earlier, clamped to the month's last day."""
    index = day.year * 12 + day.month - 1 - months
    year, month = divmod(index, 12)
    return day.replace(year=year, month=month + 1, day=min(day.day, calendar.monthrange(year, month + 1)[1]))


def parse_age(token):
    """`1y` or `6m` as a number of months, or None."""
    text = token.strip().lower()
    if len(text) < 2 or not text[:-1].isdigit() or text[-1] not in "ym" or int(text[:-1]) == 0:
        return None
    return int(text[:-1]) * (12 if text[-1] == "y" else 1)


def day_window_ms(day):
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


def quotable(message, me_id, prefix):
    text = (message.get("content") or "").strip()
    return (
        MIN_QUOTE <= len(text) <= MAX_QUOTE and message.get("author_id") not in (None, me_id)
        and not text.startswith(prefix) and not message.get("deleted") and "http" not in text
    )


def pick(candidates, seed):
    """One candidate, the same for the same seed, so asking twice in a day shows the same message."""
    if not candidates:
        return None
    ordered = sorted(candidates, key=lambda m: m["id"])
    digest = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return ordered[digest % len(ordered)]


def month_bounds(year, month):
    start = datetime(year, month, 1, tzinfo=timezone.utc)
    end = datetime(year + month // 12, month % 12 + 1, 1, tzinfo=timezone.utc)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def summarise(per_channel):
    """Numbers for a recap from {channel name: messages}; totals only, nothing per person."""
    total = sum(len(m) for m in per_channel.values())
    busiest = max(per_channel.items(), key=lambda kv: len(kv[1]), default=(None, []))
    emoji = Counter()
    best = None
    days = Counter()
    for name, messages in per_channel.items():
        for m in messages:
            reacted = sum(r["count"] for r in m.get("reactions") or [])
            for r in m.get("reactions") or []:
                emoji[r["emoji"]] += r["count"]
            days[datetime.fromtimestamp(m["created_at"] / 1000, timezone.utc).date()] += 1
            if reacted and (best is None or reacted > best[0]):
                best = (reacted, name, m)
    return {
        "total": total, "busiest": busiest[0] if busiest[1] else None, "busiest_count": len(busiest[1]),
        "emoji": emoji.most_common(3), "best": best, "busy_day": days.most_common(1)[0] if days else None,
    }
