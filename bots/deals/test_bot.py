#!/usr/bin/env python3
"""Commands and both loops against FakeAsyncClient and a scripted transport; run directly: python3 test_bot.py."""

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as deals  # noqa: E402
import db  # noqa: E402
import fixtures  # noqa: E402
from slimbots.authors import AuthorFilter  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.space import Space  # noqa: E402
from slimbots.testing import FakeAsyncClient  # noqa: E402

PEOPLE = {
    "u-admin": {"id": "u-admin", "username": "admin", "display_name": "Admin", "is_bot": False, "is_webhook": False},
    "u-ann": {"id": "u-ann", "username": "ann", "display_name": "Ann", "is_bot": False, "is_webhook": False},
    "u-bob": {"id": "u-bob", "username": "bob", "display_name": "Bob", "is_bot": False, "is_webhook": False},
}
EPIC, STEAM, GOG = "freeGamesPromotions", "platform=steam", "platform=gog"

def price_body(app_id, name="Portal 2", cents=999, currency="USD", discount=0):
    data = {"type": "game", "name": name, "steam_appid": app_id, "is_free": False, "price_overview": {"currency": currency, "initial": cents, "final": cents, "discount_percent": discount}}
    return json.dumps({str(app_id): {"success": True, "data": data}}).encode()

class Rig:
    """A fresh store, a recording client and a transport that answers from `self.web` by URL substring."""

    def __init__(self):
        self.client = FakeAsyncClient(me_id="bot-1")
        self.client.respond("GET", "/channels", [{"id": "c1", "name": "deals", "kind": "text", "category_id": None, "created_at": 0}])
        self.client.respond("GET", "/categories", [])
        deals.bot.channels = None
        deals.bot.client = self.client
        deals.bot.space = Space(self.client)
        deals.bot.authors = AuthorFilter(self.client, space=deals.bot.space)
        deals.bot.store = None
        deals.bot.data_path = os.path.join(tempfile.mkdtemp(), "deals.db")
        asyncio.run(deals.bot.space.refresh_members())
        asyncio.run(deals.bot.open_store(migrate=db.init_db))
        deals.STEAM_DELAY = 0
        self.web = {EPIC: (200, fixtures.raw("epic_free_games")), STEAM: (200, fixtures.raw("gamerpower_steam")), GOG: (201, fixtures.raw("gamerpower_gog_empty"))}
        self.calls = []
        deals.transport = self.transport

    def transport(self, url, headers):
        self.calls.append(url)
        for key, answer in self.web.items():
            if key in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return 404, b""

    def price(self, app_id, **kwargs):
        self.web[f"appids={app_id}&"] = (200, price_body(app_id, **kwargs))

    def say(self, text, user="u-admin", permissions=Permissions.MANAGE_SERVER, channel="c1", keep_cooldowns=False):
        for command in () if keep_cooldowns else deals.bot.commands.values():
            for each in [command, *getattr(command, "unique_subcommands", lambda: [])()]:
                if each.cooldown is not None:
                    each.cooldown._last.clear()
        self.client.respond("GET", f"/users/{user}", PEOPLE[user])
        deals.bot.space.members.pop(user, None)
        member = deals.bot.space._make_member({**PEOPLE[user], "role_ids": []})
        member._base_permissions = permissions
        deals.bot.space.members[user] = member
        asyncio.run(deals.bot.process_message({"id": "m1", "channel_id": channel, "author_id": user, "content": text, "seq": 1}))

    def sql(self, fn, *args):
        return asyncio.run(deals.bot.store.run(fn, *args))

    def freebies(self):
        return asyncio.run(deals.check_freebies())

    def watches(self):
        return asyncio.run(deals.check_watches())

    def setup(self):
        self.say("!deals setup")

    def titles(self):
        return [m["embeds"][0]["title"] for m in self.client.sent if m.get("embeds")]

    def pings(self):
        return [m["content"] for m in self.client.sent if not m.get("embeds") and "target" in m["content"]]

    def last_private(self):
        return self.client.ephemerals[-1]["content"]

    def last_reply(self):
        return self.client.sent[-1]["content"]

def test_setup_needs_manage_server_and_sets_the_channel():
    rig = Rig()
    rig.say("!deals setup", permissions=0)
    assert "Manage Server" in rig.last_private() and rig.sql(db.get_config, "channel_id") is None
    rig.setup()
    assert rig.sql(db.get_config, "channel_id") == "c1" and "this channel" in rig.last_reply()

def test_nothing_is_fetched_or_posted_before_setup():
    rig = Rig()
    assert rig.freebies() == (0, {}) and rig.calls == [] and rig.client.sent == []

def test_live_free_games_post_once_with_embed_link_and_end_date():
    rig = Rig()
    rig.setup()
    posted, errors = rig.freebies()
    assert posted == 3 and errors == {}
    assert sorted(rig.titles()) == ["Agent A: A puzzle in disguise", "Bad Cheese", "Dwarven Realms (Steam) Key Giveaway"]
    bad = next(m for m in rig.client.sent if m.get("embeds") and m["embeds"][0]["title"] == "Bad Cheese")
    assert bad["content"] == "https://store.epicgames.com/en-US/p/bad-cheese-4256d9"
    assert "Epic Games Store" in bad["embeds"][0]["description"] and "ends 2026-10-22 15:00 UTC" in bad["embeds"][0]["description"]
    assert rig.freebies()[0] == 0, "already posted"

def test_a_restart_does_not_repost():
    rig = Rig()
    rig.setup()
    rig.freebies()
    path = deals.bot.data_path
    deals.bot.store = None
    deals.bot.data_path = path
    asyncio.run(deals.bot.open_store(migrate=db.init_db))
    before = len(rig.client.sent)
    assert rig.freebies()[0] == 0 and len(rig.client.sent) == before

def test_a_new_offer_posts_alone_and_a_refused_post_retries():
    rig = Rig()
    rig.setup()
    rig.freebies()
    rig.web[GOG] = (200, fixtures.raw("gamerpower_gog"))
    rig.client.respond("POST", "/channels/c1/messages", ApiError(403, {"error": "forbidden"}))
    assert rig.freebies()[0] == 0, "refused, so not marked seen"
    rig.client._responses.pop(("POST", "/channels/c1/messages"))
    assert rig.freebies()[0] == 1 and rig.titles()[-1] == "Example Quest (GOG) Giveaway"

def test_a_failing_source_is_reported_and_the_others_still_post():
    rig = Rig()
    rig.setup()
    rig.web[EPIC] = (500, b"")
    posted, errors = rig.freebies()
    assert posted == 1 and errors == {"epic": "http 500"}

def test_sources_command_limits_checks_and_rejects_unknown_names():
    rig = Rig()
    rig.setup()
    rig.say("!deals sources steam")
    assert asyncio.run(deals.active_sources()) == ["steam"]
    rig.freebies()
    assert rig.titles() == ["Dwarven Realms (Steam) Key Giveaway"] and not any(EPIC in u for u in rig.calls)
    rig.say("!deals sources epic,origin")
    assert "pick from" in rig.last_reply() and asyncio.run(deals.active_sources()) == ["steam"]
    rig.say("!deals sources epic", permissions=0)
    assert "Manage Server" in rig.last_private()

def test_an_at_sign_in_a_title_cannot_ping_anyone():
    rig = Rig()
    rig.setup()
    steam = fixtures.load("gamerpower_steam")
    steam[0]["title"] = "Hi @everyone game"
    rig.web[STEAM] = (200, json.dumps(steam).encode())
    rig.freebies()
    assert all("@" not in t for t in rig.titles())

def test_deals_now_checks_everything_and_reports_a_failing_source():
    rig = Rig()
    rig.say("!deals now")
    assert "setup" in rig.last_reply()
    rig.setup()
    rig.web[EPIC] = (500, b"")
    rig.price(620)
    rig.say("!watch 620 5", user="u-ann", permissions=0)
    rig.say("!deals now")
    assert "posted 1 new" in rig.last_private() and "Epic Games Store failed: http 500" in rig.last_private()
    rig.say("!deals now", permissions=0)
    assert "Manage Server" in rig.last_private()
    rig.say("!deals now", keep_cooldowns=True)
    rig.say("!deals now", keep_cooldowns=True)
    assert "slow down" in rig.last_reply()

def test_watch_add_by_link_and_by_id_then_list_and_remove_are_private_and_per_user():
    rig = Rig()
    rig.price(620)
    rig.say("!watch https://store.steampowered.com/app/620/Portal_2/ $4.99", user="u-ann", permissions=0)
    assert "watching **Portal 2** as `1`" in rig.last_private() and "9.99 USD" in rig.last_private()
    assert rig.client.sent == [], "replies stay private"
    rig.say("!watch 620 3", user="u-ann", permissions=0)
    assert "updated the watch" in rig.last_private() and len(rig.sql(db.list_watches)) == 1
    rig.say("!watch list", user="u-ann", permissions=0)
    assert "Portal 2" in rig.last_private() and "target 3.00 USD" in rig.last_private()
    rig.say("!watch list", user="u-bob", permissions=0)
    assert "no watches yet" in rig.last_private()
    rig.say("!watch remove 1", user="u-bob", permissions=0)
    assert "no watch `1`" in rig.last_private() and len(rig.sql(db.list_watches)) == 1
    rig.say("!watch remove 1", user="u-ann", permissions=0)
    assert rig.sql(db.list_watches) == []

def test_watch_add_refuses_bad_input_unavailable_free_and_outages():
    rig = Rig()
    rig.say("!watch")
    assert "usage" in rig.last_private()
    rig.say("!watch portal 5")
    assert "Steam store link" in rig.last_private()
    rig.say("!watch 620 cheap")
    assert "target price" in rig.last_private()
    rig.web["appids=1&"] = (200, fixtures.raw("appdetails_unavailable"))
    rig.say("!watch 1 5")
    assert "region locked or removed" in rig.last_private()
    rig.web["appids=730&"] = (200, fixtures.raw("appdetails_free_to_play"))
    rig.say("!watch 730 5")
    assert "free to play" in rig.last_private()
    rig.web["appids=620&"] = (429, b"")
    rig.say("!watch 620 5")
    assert "could not reach Steam (http 429)" in rig.last_private()
    assert rig.sql(db.list_watches) == []

def test_the_watch_limit_is_per_user():
    rig = Rig()
    for app_id in range(1, deals.MAX_WATCHES_PER_USER + 1):
        rig.price(app_id)
        rig.say(f"!watch {app_id} 5", user="u-ann", permissions=0)
    rig.price(99)
    rig.say("!watch 99 5", user="u-ann", permissions=0)
    assert "at most" in rig.last_private()
    rig.say("!watch 99 5", user="u-bob", permissions=0)
    assert "watching" in rig.last_private()

def test_a_drop_to_target_mentions_the_watcher_in_the_channel_once():
    rig = Rig()
    rig.setup()
    rig.price(620, cents=999)
    rig.say("!watch 620 5", user="u-ann", permissions=0)
    rig.watches()
    assert rig.pings() == [], "above target"
    rig.price(620, cents=499, discount=50)
    rig.watches()
    assert len(rig.pings()) == 1
    ping = rig.pings()[0]
    assert ping.startswith("@ann ") and "4.99 USD (50% off)" in ping and "store.steampowered.com/app/620/" in ping
    assert rig.client.sent[-1]["channel_id"] == "c1"
    rig.watches()
    assert len(rig.pings()) == 1, "same price, no repeat"

def test_a_further_drop_pings_again_and_so_does_a_drop_after_a_rise():
    rig = Rig()
    rig.setup()
    rig.price(620, cents=999)
    rig.say("!watch 620 5", user="u-ann", permissions=0)
    for cents, expected in ((499, 1), (399, 2), (450, 2), (999, 2), (450, 3)):
        rig.price(620, cents=cents)
        rig.watches()
        assert len(rig.pings()) == expected, (cents, expected)

def test_a_refused_ping_is_retried_at_the_next_check():
    rig = Rig()
    rig.setup()
    rig.price(620, cents=999)
    rig.say("!watch 620 5", user="u-ann", permissions=0)
    rig.price(620, cents=400)
    rig.client.respond("POST", "/channels/c1/messages", ApiError(403, {"error": "forbidden"}))
    rig.watches()
    assert rig.sql(db.list_watches)[0]["notified_price_cents"] is None
    rig.client._responses.pop(("POST", "/channels/c1/messages"))
    rig.watches()
    assert len(rig.pings()) == 2, "the fake records the refused attempt too"
    assert rig.sql(db.list_watches)[0]["notified_price_cents"] == 400

def test_one_lookup_serves_every_watcher_of_a_game():
    rig = Rig()
    rig.setup()
    rig.price(620, cents=999)
    for user in ("u-ann", "u-bob"):
        rig.say("!watch 620 5", user=user, permissions=0)
    rig.price(620, cents=400)
    rig.calls.clear()
    assert rig.watches() == 2 and len(rig.calls) == 1
    assert [p.split()[0] for p in rig.pings()] == ["@ann", "@bob"]

def test_a_removed_app_is_noticed_after_three_missed_days_once():
    rig = Rig()
    rig.setup()
    rig.price(620, cents=999)
    rig.say("!watch 620 5", user="u-ann", permissions=0)
    rig.web["appids=620&"] = (200, b'{"620": {"success": false}}')
    for _ in range(5):
        rig.watches()
    notices = [m["content"] for m in rig.client.sent if "not been available" in m["content"]]
    assert len(notices) == 1 and notices[0].startswith("@ann ") and "watch remove 1" in notices[0]
    assert len(rig.sql(db.list_watches)) == 1, "the watch is kept"
    rig.price(620, cents=400)
    rig.watches()
    assert len(rig.pings()) == 1 and rig.sql(db.list_watches)[0]["misses"] == 0

def test_a_price_in_another_currency_than_the_watch_is_skipped_not_compared():
    rig = Rig()
    rig.setup()
    rig.price(620, cents=999)
    rig.say("!watch 620 5", user="u-ann", permissions=0)
    rig.price(620, cents=300, currency="EUR")
    rig.watches()
    assert rig.pings() == []

def test_a_steam_outage_leaves_watches_untouched():
    rig = Rig()
    rig.setup()
    rig.price(620, cents=999)
    rig.say("!watch 620 5", user="u-ann", permissions=0)
    rig.web["appids=620&"] = (503, b"")
    rig.watches()
    watch = rig.sql(db.list_watches)[0]
    assert watch["misses"] == 0 and watch["last_price_cents"] == 999

def test_the_loops_run_when_due_and_not_again_before_their_interval():
    rig = Rig()
    rig.setup()
    assert asyncio.run(deals.due("last_freebies_at", deals.FREEBIES_INTERVAL))
    rig.freebies()
    assert not asyncio.run(deals.due("last_freebies_at", deals.FREEBIES_INTERVAL))
    assert asyncio.run(deals.due("last_watches_at", deals.WATCH_INTERVAL))
    assert deals.FREEBIES_INTERVAL == 10800 and deals.WATCH_INTERVAL == 86400

if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
