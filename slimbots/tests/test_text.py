"""`slimbots.text`: clip, one_line, mention escaping and the duration shapes bots print."""

import re

import pytest

from slimbots.text import break_mentions, clip, escape_mentions, format_duration, one_line

MENTION_START = re.compile(r"[A-Za-z0-9_]")


def live_mentions(text):
    """The server's grammar (`push/mention_grammar.rs`): `@name`, `@[Role]`, neither when an odd run of backslashes precedes the `@`."""
    found = []
    for at, char in enumerate(text):
        if char != "@":
            continue
        backslashes = len(text[:at]) - len(text[:at].rstrip("\\"))
        if backslashes % 2:
            continue
        nxt = text[at + 1:at + 2]
        if MENTION_START.fullmatch(nxt) or (nxt == "[" and "]" in text[at + 2:].split("\n")[0]):
            found.append(at)
    return found


MENTION_CASES = [
    "@everyone", "@here free pizza", "hi @nick.", "@[Core Team] ping", "@@nick", "a@b.c email", "\\@nick", "\\\\@nick",
    "\\\\\\@nick", "@", "@ alone", "@_x", "@[", "x @[Admins]\n@here", "@​nick", "路@nick",
]


@pytest.mark.parametrize("text", MENTION_CASES)
def test_escaped_text_holds_no_live_mention(text):
    assert live_mentions(escape_mentions(text)) == []


@pytest.mark.parametrize("text", MENTION_CASES)
def test_broken_text_holds_no_live_mention(text):
    assert live_mentions(break_mentions(text)) == []


@pytest.mark.parametrize("text", MENTION_CASES)
def test_escaping_twice_changes_nothing(text):
    assert escape_mentions(escape_mentions(text)) == escape_mentions(text)


def test_the_oracle_itself_sees_the_live_ones():
    assert live_mentions("@everyone and @[Core Team]") == [0, 14]
    assert live_mentions("\\@nick and \\\\@nick") == [13]


def test_an_already_escaped_mention_keeps_its_one_backslash():
    assert escape_mentions("\\@bob") == "\\@bob"
    assert escape_mentions("\\\\@bob") == "\\\\\\@bob"
    assert escape_mentions("@everyone") == "\\@everyone"
    assert escape_mentions("no mention here") == "no mention here"


def test_breaking_a_mention_inserts_a_zero_width_space():
    assert break_mentions("@everyone") == "@​everyone"


@pytest.mark.parametrize("text, limit, ellipsis, expected", [
    ("short", 10, "...", "short"),
    ("exactly10!", 10, "...", "exactly10!"),
    ("a longer sentence here", 10, "...", "a longe..."),
    ("hello world foo", 9, "...", "hello..."),
    ("x" * 100, 80, "~", "x" * 79 + "~"),
    ("abcdef", 2, "...", "..."),
    ("", 5, "...", ""),
])
def test_clip(text, limit, ellipsis, expected):
    assert clip(text, limit, ellipsis) == expected


def test_a_clipped_text_never_runs_past_its_limit_except_for_a_limit_below_the_ellipsis():
    for limit in range(3, 40):
        assert len(clip("word " * 30, limit)) <= limit


def test_one_line_collapses_every_run_of_whitespace():
    assert one_line("  a \n b\t\tc  ") == "a b c"
    assert one_line(None) == ""
    assert one_line(12) == "12"


def old_modlog(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{minutes}m" if minutes else f"{hours}h"
    days, hours = divmod(hours, 24)
    return f"{days}d{hours}h" if hours else f"{days}d"


def old_casino(seconds):
    seconds = max(0, int(seconds))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def old_feeds(seconds):
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    return f"{max(seconds // 60, 1)}m"


def old_reminders(seconds):
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def old_uptime_relay(seconds):
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes} min" if minutes else f"{hours} h"


def old_pelican(milliseconds):
    minutes = int(milliseconds // 60000)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m"


SAMPLES = sorted({*range(0, 200), *range(3590, 3700), *range(86300, 86500), *range(172700, 173000), 604800, 1_000_000, 31_536_000, 45, 90, 150, 3599, 3600, 7260})


@pytest.mark.parametrize("style, oracle", [
    ("compact", old_modlog), ("spaced", old_casino), ("interval", old_feeds), ("exact", old_reminders), ("minutes", old_uptime_relay),
])
def test_every_style_prints_what_the_bot_it_came_from_printed(style, oracle):
    for seconds in SAMPLES:
        assert format_duration(seconds, style) == oracle(seconds), (style, seconds)


def test_the_uptime_style_matches_pelican_given_seconds():
    for milliseconds in [0, 59_999, 60_000, 3_600_000, 3_660_000, 86_400_000, 90_061_000, 400_000_000]:
        assert format_duration(milliseconds // 1000, "uptime") == old_pelican(milliseconds)


def test_the_minutes_style_rounds_a_fractional_count_like_the_original():
    for seconds in [29.9, 30.0, 89.9, 90.5, 150.4, 3599.5]:
        assert format_duration(seconds, "minutes") == old_uptime_relay(seconds)


def test_an_unknown_style_is_refused():
    with pytest.raises(ValueError):
        format_duration(5, "long")
