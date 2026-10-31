#!/usr/bin/env python3
"""History windows, the quote picker, the recap and both commands against FakeAsyncClient; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
from datetime import date, datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as memories  # noqa: E402
import history  # noqa: E402
from slimbots.testing import FakeAsyncClient, human, run_module_tests, wire_bot  # noqa: E402

ANN = human("u-ann", "ann", "Ann")
HEART = chr(0x2764)
CHANNELS = [
    {"id": "c-gen", "name": "general", "kind": "text", "category_id": None, "created_at": 0, "restricted": False},
    {"id": "c-priv", "name": "staff", "kind": "text", "category_id": None, "created_at": 0, "restricted": True},
    {"id": "c-unk", "name": "unknown", "kind": "text", "category_id": None, "created_at": 0},
]
_seq = [0]


def ms(year, month, day, hour=12):
    return int(datetime(year, month, day, hour, tzinfo=timezone.utc).timestamp() * 1000)


def msg(when, text, author="u-ann", reactions=None):
    _seq[0] += 1
    return {"id": f"id{_seq[0]:04d}", "seq": _seq[0], "channel_id": "c-gen", "author_id": author, "content": text, "created_at": when, "reactions": reactions or []}


class HistoryClient(FakeAsyncClient):
    """Serves newest-first pages per channel from a dict, honouring `before` and `limit` like the server."""

    def __init__(self, store):
        super().__init__(me_id="bot-1")
        self.store = store
        self.pages = []

    async def call(self, method, path, body=None, **kw):
        if method == "GET" and path.startswith("/channels/") and path.endswith("/messages"):
            channel_id = path.split("/")[2]
            params = kw.get("params") or {}
            self.pages.append(channel_id)
            rows = sorted(self.store.get(channel_id, []), key=lambda m: -m["seq"])
            rows = [m for m in rows if "before" not in params or m["seq"] < params["before"]]
            return rows[: params.get("limit", 50)]
        return await super().call(method, path, body, **kw)


def setup(store, ages=(12,)):
    memories.AGES, memories.EXTRA_PUBLIC = list(ages), []
    memories.datetime = type("D", (datetime,), {"now": classmethod(lambda cls, tz=None: datetime(2026, 11, 4, 9, tzinfo=tz))})

    async def no_gateway(frame):
        return None

    memories.bot.send_frame = no_gateway
    client = HistoryClient(store)
    client.respond("GET", "/channels", CHANNELS)
    client.respond("GET", "/categories", [])
    client.respond("GET", "/users/u-ann", {**ANN, "role_ids": []})
    wire_bot(memories.bot, client, me_id="bot-1", db_name="memories.db")
    asyncio.run(memories.bot.space.refresh_channels())
    return client


def say(text):
    for command in memories.bot.commands.values():
        getattr(command, "cooldown", None) and command.cooldown._last.clear()
    asyncio.run(memories.bot.process_message({"id": "m-cmd", "channel_id": "c-gen", "author_id": "u-ann", "content": text, "seq": 1}))


def test_months_before_clamps_to_the_month_end():
    assert history.months_before(date(2026, 3, 31), 1) == date(2026, 2, 28)
    assert history.months_before(date(2026, 1, 15), 2) == date(2025, 11, 15)
    assert history.months_before(date(2026, 11, 4), 12) == date(2025, 11, 4)


def test_ages_parse_and_bad_ones_are_dropped():
    assert [history.parse_age(t) for t in ("1y", "6m", "0m", "x", "12")] == [12, 6, None, None, None]


def test_fetch_window_pages_back_and_stops_past_the_window():
    rows = [msg(ms(2025, 11, 4, h % 24), f"old message number {h}") for h in range(150)] + [msg(ms(2026, 1, 1), "newer than the window one")] + [msg(ms(2025, 10, 1), "far too old for this window ok")]
    rows = sorted(rows, key=lambda m: m["created_at"])
    for i, m in enumerate(rows):
        m["seq"] = i + 1
    client = setup({"c-gen": rows})
    start, end = history.day_window_ms(date(2025, 11, 4))
    got = asyncio.run(history.fetch_window(client, "c-gen", start, end, 10))
    assert len(got) == 150


def test_onthisday_quotes_a_public_message_from_a_year_ago_and_defuses_mentions():
    old = msg(ms(2025, 11, 4), "we should all @everyone watch that film again soon")
    private = {**msg(ms(2025, 11, 4), "private staff chatter that must never be quoted here"), "channel_id": "c-priv"}
    client = setup({"c-gen": [old], "c-priv": [private]})
    say("!onthisday")
    text = client.sent[-1]["content"]
    assert "1 year ago, in #general" in text and "watch that film again" in text and "@everyone" not in text
    assert "c-priv" not in client.pages and "c-unk" not in client.pages


def test_onthisday_falls_back_to_a_shorter_age_and_then_to_a_sentence():
    client = setup({"c-gen": [msg(ms(2026, 5, 4), "six months ago we planned the trip properly")]}, ages=(12, 6))
    say("!onthisday")
    assert "6 months ago" in client.sent[-1]["content"]
    client = setup({"c-gen": [msg(ms(2026, 5, 4), "short")]}, ages=(12, 6))
    say("!onthisday")
    assert "could not find" in client.sent[-1]["content"]


def test_quotable_skips_bot_commands_links_and_the_bot_itself():
    ok = msg(ms(2025, 1, 1), "a perfectly ordinary sentence here")
    assert history.quotable(ok, "bot-1", "!")
    assert not history.quotable({**ok, "author_id": "bot-1"}, "bot-1", "!")
    assert not history.quotable({**ok, "content": "!remind in 5m this is a command line"}, "bot-1", "!")
    assert not history.quotable({**ok, "content": "look at https://example.com/page now"}, "bot-1", "!")


def test_the_same_day_picks_the_same_quote():
    pool = [msg(ms(2025, 1, 1), f"message number {i} in the pool") for i in range(8)]
    assert history.pick(pool, "2026-11-04:12") == history.pick(list(reversed(pool)), "2026-11-04:12")


def test_recap_counts_channels_emoji_and_the_most_reacted_message_without_naming_anyone():
    rows = [msg(ms(2026, 10, d), f"talk on day {d} of the month") for d in (2, 2, 2, 9)]
    rows.append(msg(ms(2026, 10, 9), "the funniest thing anyone said", reactions=[{"emoji": HEART, "count": 5, "reacted": False}]))
    rows.append(msg(ms(2026, 9, 30), "outside the month entirely, not counted"))
    client = setup({"c-gen": rows, "c-priv": [msg(ms(2026, 10, 3), "staff only")]})
    say("!recap 2026-10")
    text = client.sent[-1]["content"]
    assert "October 2026" in text and "5 messages" in text and "busiest channel: #general (5)" in text
    assert "busiest day: October 2 (3 messages)" in text and f"{HEART} x5" in text
    assert "the funniest thing anyone said" in text and "@ann" not in text and "Ann" not in text


def test_a_future_or_malformed_month_is_refused_privately():
    client = setup({})
    say("!recap 2999-01")
    assert "past month" in client.ephemerals[-1]["content"]
    say("!recap banana")
    assert "past month" in client.ephemerals[-1]["content"]
    assert not client.sent


def test_an_empty_month_says_so():
    client = setup({"c-gen": []})
    say("!recap 2026-10")
    assert "nothing was said" in client.sent[-1]["content"]


def test_the_daily_post_goes_out_once():
    client = setup({"c-gen": [msg(ms(2025, 11, 4), "a message worth bringing back again")]})
    memories.POST_CHANNEL, memories.POST_HOUR, memories.MONTHLY = "c-gen", 0, False
    asyncio.run(memories.daily())
    asyncio.run(memories.daily())
    assert len([s for s in client.sent if "On this day" in s["content"]]) == 1


if __name__ == "__main__":
    run_module_tests(globals())
