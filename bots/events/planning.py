"""Parsing `"movie night" fri 8pm` into a title and a start time; pure, so tests pin the clock."""

import shlex
from datetime import datetime, time, timedelta

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
MAX_DAYS_AHEAD = 60
MAX_TITLE = 80


def parse_time(token):
    """`8pm`, `8:30pm`, `20:00` as a time of day, or None."""
    text = token.lower()
    meridiem = text[-2:] if text[-2:] in ("am", "pm") else ""
    clock = text[: -len(meridiem)] if meridiem else text
    hour_text, _, minute_text = clock.partition(":")
    if not hour_text.isdigit() or (minute_text and not minute_text.isdigit()):
        return None
    hour, minute = int(hour_text), int(minute_text or 0)
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if meridiem == "pm" else 0)
    elif ":" not in text:
        return None
    if hour > 23 or minute > 59:
        return None
    return time(hour, minute)


def parse_day(token, today):
    text = token.lower()
    if text == "today":
        return today
    if text == "tomorrow":
        return today + timedelta(days=1)
    if text[:3] in WEEKDAYS and (len(text) == 3 or text.startswith(("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))):
        return today + timedelta(days=(WEEKDAYS.index(text[:3]) - today.weekday()) % 7)
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def parse_event(text, now):
    """(title, start) from `[title] <day> <time>`, `now` being timezone-aware; a ValueError carries a sentence for the member."""
    try:
        tokens = shlex.split(text)
    except ValueError:
        tokens = text.split()
    if len(tokens) < 3:
        raise ValueError('say it like `"movie night" fri 8pm`: a title, a day and a time.')
    when, day_token, title = parse_time(tokens[-1]), tokens[-2], " ".join(tokens[:-2]).strip()
    day = parse_day(day_token, now.date())
    if when is None or day is None:
        raise ValueError("I could not read the day and time; use a weekday, `today`, `tomorrow` or YYYY-MM-DD, then 8pm or 20:00.")
    start = datetime.combine(day, when, tzinfo=now.tzinfo)
    if start <= now and day_token.lower()[:3] in WEEKDAYS:
        start += timedelta(days=7)
    if start <= now:
        raise ValueError("that time has already passed.")
    if start - now > timedelta(days=MAX_DAYS_AHEAD):
        raise ValueError(f"keep events within {MAX_DAYS_AHEAD} days.")
    if not title or len(title) > MAX_TITLE:
        raise ValueError(f"the title must be 1 to {MAX_TITLE} characters.")
    return title.replace("@", " at "), start
