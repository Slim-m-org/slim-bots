"""Small text helpers bots keep rewriting: clip, one-line, mention escaping, and durations in the few shapes bots print."""

from __future__ import annotations

ZERO_WIDTH_SPACE = "​"


def one_line(text: object) -> str:
    """Collapses every run of whitespace, newlines included, to a single space."""
    return " ".join(str(text or "").split())


def clip(text: str, limit: int, ellipsis: str = "...") -> str:
    """`text` cut to at most `limit` characters, ending in `ellipsis` when it was cut."""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(ellipsis))].rstrip() + ellipsis


def escape_mentions(text: str) -> str:
    """Backslash-escapes every `@` the server would read as a mention, in prose.
    An `@` already escaped by an odd run of backslashes is left alone, so escaping twice changes nothing."""
    out: list[str] = []
    backslashes = 0
    for char in text:
        if char == "@" and backslashes % 2 == 0:
            out.append("\\")
        out.append(char)
        backslashes = backslashes + 1 if char == "\\" else 0
    return "".join(out)


def break_mentions(text: str) -> str:
    """Puts a zero-width space after every `@`, which no mention grammar reads; for a code span, where a backslash would show."""
    return text.replace("@", "@" + ZERO_WIDTH_SPACE)


def _exact(seconds: float) -> str:
    seconds = int(seconds)
    for size, unit in ((86400, "d"), (3600, "h"), (60, "m")):
        if seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s"


def _interval(seconds: float) -> str:
    seconds = int(seconds)
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{max(seconds // 60, 1)}m"


def _compact(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h" if hours else f"{days}d"


def _spaced(seconds: float) -> str:
    seconds = int(seconds)
    hours, seconds = divmod(max(0, seconds), 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}h {minutes}m"
    return f"{minutes}m" if minutes else f"{seconds}s"


def _minutes(seconds: float) -> str:
    total = max(1, round(seconds / 60))
    if total < 60:
        return f"{total} min"
    hours, minutes = divmod(total, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def _uptime(seconds: float) -> str:
    seconds = int(seconds)
    minutes = seconds // 60
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m"


_DURATION_STYLES = {
    "exact": _exact, "interval": _interval, "compact": _compact, "spaced": _spaced, "minutes": _minutes, "uptime": _uptime,
}


def format_duration(seconds: float, style: str = "exact") -> str:
    """A count of seconds as the text the bots print; each `style` is one shape, kept so replies read as they always did.
    exact `1h`, interval `5m` (whole minutes), compact `1h5m`, spaced `1h 5m`, minutes `1 h 5 min`, uptime `2d 3h`."""
    try:
        shape = _DURATION_STYLES[style]
    except KeyError:
        raise ValueError(f"unknown duration style {style!r}") from None
    return shape(seconds)
