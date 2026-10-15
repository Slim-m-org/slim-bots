#!/usr/bin/env python3
"""Commands and the poll loop against FakeAsyncClient and a scripted transport; run directly: python3 test_bot.py."""

import asyncio

import bot as feeds
import db
import fixtures
from slimbots.http import ApiError
from slimbots.permissions import Permissions
from slimbots.testing import FakeAsyncClient, human, run_module_tests, wire_bot

URL = "https://example.com/feed.xml"
ADMIN = human("u-admin", "admin", "Admin")


class Rig:
    """A fresh store, a recording client and a transport that answers from `self.web` by URL."""

    def __init__(self):
        self.client = FakeAsyncClient(me_id="bot-1")
        self.client.respond("GET", "/channels", [{"id": "c1", "name": "news", "kind": "text", "category_id": None, "created_at": 0}])
        self.client.respond("GET", "/categories", [])
        feeds.bot.channels = None
        wire_bot(feeds.bot, self.client, db_name="feeds.db")
        asyncio.run(feeds.bot.space.refresh_members())
        asyncio.run(feeds.bot.open_store(migrate=db.init_db))
        self.web = {}
        self.calls = []
        feeds.transport = self.transport

    def transport(self, url, headers):
        self.calls.append((url, headers))
        answer = self.web.get(url, (404, {}, b""))
        if isinstance(answer, Exception):
            raise answer
        if "if-none-match" in headers and answer[1].get("etag") == headers["if-none-match"]:
            return 304, {}, b""
        return answer

    def serve(self, body, url=URL, etag=None):
        self.web[url] = (200, {"etag": etag} if etag else {}, body)

    def say(self, text, permissions=Permissions.MANAGE_SERVER, channel="c1"):
        self.client.respond("GET", "/users/u-admin", ADMIN)
        feeds.bot.space.members.pop("u-admin", None)
        member = feeds.bot.space._make_member({**ADMIN, "role_ids": []})
        member._base_permissions = permissions
        feeds.bot.space.members["u-admin"] = member
        asyncio.run(feeds.bot.process_message({"id": "m1", "channel_id": channel, "author_id": "u-admin", "content": text, "seq": 1}))

    def sql(self, fn, *args):
        return asyncio.run(feeds.bot.store.run(fn, *args))

    def make_due(self):
        self.sql(lambda conn: (conn.execute("UPDATE feeds SET next_due = 0"), conn.commit()))

    def poll(self):
        self.make_due()
        asyncio.run(feeds.poll_due())

    def titles(self):
        return [m["embeds"][0]["title"] for m in self.client.sent if m.get("embeds")]

    def last_reply(self):
        return self.client.sent[-1]["content"]


def test_adding_a_feed_posts_only_the_latest_item_then_new_ones_as_they_appear():
    rig = Rig()
    rig.serve(fixtures.RSS)
    rig.say(f"!feed add {URL}")
    assert rig.titles() == ["Second & newer"]
    assert "Only the latest item is posted now" in rig.last_reply()
    rig.poll()
    assert rig.titles() == ["Second & newer"], "the older item was recorded as seen, not posted"
    rig.serve(fixtures.rss_with(("g-3", "Third", "x"), ("g-2", "Second", "x"), ("g-1", "First", "x")))
    rig.poll()
    rig.poll()
    assert rig.titles() == ["Second & newer", "Third"]


def test_the_embed_carries_title_link_trimmed_summary_and_source_name():
    rig = Rig()
    rig.serve(fixtures.rss_with(("g-1", "Big", "word " * 200)))
    rig.say(f"!feed add {URL}")
    sent = rig.client.sent[0]
    embed = sent["embeds"][0]
    assert embed["url"] == "https://example.com/g-1" and embed["footer"]["text"] == "Example Blog"
    assert len(embed["description"]) <= feeds.SUMMARY_LIMIT
    assert sent["channel_id"] == "c1"


def test_overflow_is_capped_oldest_first_and_never_reposted():
    rig = Rig()
    rig.serve(fixtures.rss_with(("g-0", "Zero", "x")))
    rig.say(f"!feed add {URL}")
    items = [(f"n-{i}", f"New {i}", "x") for i in range(9, 0, -1)] + [("g-0", "Zero", "x")]
    rig.serve(fixtures.rss_with(*items))
    rig.poll()
    posted = rig.titles()[1:]
    assert len(posted) == feeds.MAX_POSTS_PER_POLL and posted == ["New 5", "New 6", "New 7", "New 8", "New 9"]
    rig.poll()
    assert len(rig.titles()) == 1 + feeds.MAX_POSTS_PER_POLL


def test_the_keyword_filter_limits_what_posts_and_filtered_items_are_not_retried():
    rig = Rig()
    rig.serve(fixtures.rss_with(("g-0", "Zero", "x")))
    rig.say(f"!feed add {URL} rust")
    assert rig.titles() == [], "the only item does not match"
    rig.serve(fixtures.rss_with(("g-2", "Rust 2", "x"), ("g-1", "Python", "x"), ("g-0", "Zero", "x")))
    rig.poll()
    assert rig.titles() == ["Rust 2"]
    rig.say("!feed filter 1 off")
    rig.poll()
    assert rig.titles() == ["Rust 2"], "python was seen while filtered, so clearing the filter does not backfill"


def test_a_restart_does_not_repost():
    rig = Rig()
    rig.serve(fixtures.RSS)
    rig.say(f"!feed add {URL}")
    path = feeds.bot.data_path
    feeds.bot.store = None
    feeds.bot.data_path = path
    asyncio.run(feeds.bot.open_store(migrate=db.init_db))
    rig.poll()
    assert rig.titles() == ["Second & newer"]


def test_conditional_get_uses_the_stored_etag_and_a_304_is_a_success():
    rig = Rig()
    rig.serve(fixtures.RSS, etag='"v1"')
    rig.say(f"!feed add {URL}")
    rig.poll()
    assert rig.calls[-1][1]["if-none-match"] == '"v1"'
    assert rig.sql(db.get_feed, 1)["failures"] == 0


def test_a_failing_feed_does_not_stop_the_loop_and_is_surfaced_in_the_list():
    rig = Rig()
    other = "https://other.example/feed.xml"
    rig.serve(fixtures.RSS)
    rig.say(f"!feed add {URL}")
    rig.serve(fixtures.rss_with(("o-1", "Other one", "x")), url=other)
    rig.say(f"!feed add {other}")
    before = len(rig.titles())
    rig.web[URL] = OSError("down")
    rig.serve(fixtures.rss_with(("o-2", "Other two", "x"), ("o-1", "Other one", "x")), url=other)
    for _ in range(feeds.db.FAILING_AFTER):
        rig.poll()
    assert rig.titles()[before:] == ["Other two"], "the healthy feed kept posting"
    rig.say("!feed list")
    listing = rig.last_reply()
    assert "FAILING" in listing and "could not connect" in listing and "OSError" in listing
    assert "other.example" in listing and "/feed.xml" not in listing, "only the host is shown, never a tokened path"


def test_an_exception_inside_one_feed_is_logged_and_the_loop_survives():
    rig = Rig()
    rig.serve(fixtures.RSS)
    rig.say(f"!feed add {URL}")
    original = feeds.deliver

    async def explode(*args):
        raise RuntimeError("bug")

    feeds.deliver = explode
    try:
        rig.serve(fixtures.rss_with(("g-9", "New", "x")))
        rig.poll()
    finally:
        feeds.deliver = original
    assert "internal error" in rig.sql(db.get_feed, 1)["last_error"]


def test_a_refused_post_keeps_the_entry_for_retry_and_marks_the_feed():
    rig = Rig()
    rig.serve(fixtures.rss_with(("g-0", "Zero", "x")))
    rig.say(f"!feed add {URL}")
    rig.serve(fixtures.rss_with(("g-1", "One", "x"), ("g-0", "Zero", "x")))
    rig.client.respond("POST", "/channels/c1/messages", ApiError(403, {"error": "forbidden"}))
    rig.poll()
    assert "could not post" in rig.sql(db.get_feed, 1)["last_error"]
    rig.client._responses.clear()
    rig.poll()
    assert rig.titles()[-1] == "One"


def test_a_revoked_token_still_stops_the_bot():
    rig = Rig()
    rig.serve(fixtures.rss_with(("g-0", "Zero", "x")))
    rig.say(f"!feed add {URL}")
    rig.serve(fixtures.rss_with(("g-1", "One", "x"), ("g-0", "Zero", "x")))
    rig.client.respond("POST", "/channels/c1/messages", ApiError(401, {"error": "unauthorized"}))
    try:
        rig.poll()
    except ApiError as err:
        assert err.status == 401
    else:
        raise AssertionError("401 must propagate")


def test_a_member_without_manage_server_cannot_change_feeds_but_can_list():
    rig = Rig()
    rig.serve(fixtures.RSS)
    for text in (f"!feed add {URL}", "!feed remove 1", "!feed filter 1 x", "!feed interval 1 1h"):
        rig.say(text, permissions=0)
        assert "Manage Server" in rig.client.ephemerals[-1]["content"], text
    assert rig.client.sent == [] and rig.sql(db.list_feeds) == []
    rig.say("!feed list", permissions=0)
    assert "no feeds yet" in rig.last_reply()


def test_add_rejects_bad_urls_unreadable_feeds_and_duplicates():
    rig = Rig()
    rig.say("!feed add ftp://x/y")
    assert "http" in rig.last_reply()
    rig.say(f"!feed add {URL}")
    assert "could not read that feed: http 404" in rig.last_reply()
    rig.serve(b"<html>nope</html>")
    rig.say(f"!feed add {URL}")
    assert "not an RSS or Atom feed" in rig.last_reply()
    rig.serve(fixtures.RSS)
    rig.say(f"!feed add {URL}")
    rig.say(f"!feed add {URL}")
    assert "already follows" in rig.last_reply()


def test_add_normalises_a_youtube_channel_url_to_its_feed():
    rig = Rig()
    feed_url = "https://www.youtube.com/feeds/videos.xml?channel_id=UCabcdefghijklmnopqrstuv"
    rig.serve(fixtures.ATOM, url=feed_url)
    rig.say("!feed add https://www.youtube.com/channel/UCabcdefghijklmnopqrstuv")
    assert rig.titles() == ["Video B"]


def test_remove_and_interval_commands():
    rig = Rig()
    rig.serve(fixtures.RSS)
    rig.say(f"!feed add {URL}")
    rig.say("!feed interval 1 1m")
    assert "pick between 10m and 7d" in rig.last_reply()
    rig.say("!feed interval 1 2h")
    assert rig.sql(db.get_feed, 1)["interval"] == 7200
    rig.say("!feed remove 1")
    assert rig.sql(db.list_feeds) == []
    rig.say("!feed remove 1")
    assert "no feed" in rig.last_reply()


def test_seen_entries_are_pruned_to_a_bound():
    rig = Rig()
    rig.sql(db.add_feed, "c1", URL, "n", "", 600, "u")
    rig.sql(db.mark_seen, 1, [f"k{i}" for i in range(db.KEEP_SEEN + 50)])
    assert len(rig.sql(db.seen_keys, 1)) == db.KEEP_SEEN


if __name__ == "__main__":
    run_module_tests(globals())
