#!/usr/bin/env python3
"""Commands and the presence poll against FakeAsyncClient and a scripted Steam; run directly: python3 test_bot.py."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as cs2  # noqa: E402
import steam  # noqa: E402
from slimbots.testing import FakeAsyncClient, human, run_module_tests, wire_bot  # noqa: E402

ANN = human("u-ann", "ann", "Ann")
BOB = human("u-bob", "bob", "Bob")
CAL = human("u-cal", "cal", "Cal")
ID_ANN, ID_BOB, ID_CAL = "76561198000000001", "76561198000000002", "76561198000000003"


class Rig:
    def __init__(self, **config):
        for name, value in {"STEAM_API_KEY": "KEY123", "ANNOUNCE_CHANNEL": "c-announce", "LFG_ROLE": "", "LFG_MIN": 2, "SERVER": ""}.items():
            setattr(cs2, name, config.get(name, value))
        self.client = FakeAsyncClient(me_id="bot-1")
        for person in (ANN, BOB, CAL):
            self.client.respond("GET", f"/users/{person['id']}", {**person, "role_ids": []})
        wire_bot(cs2.bot, self.client, db_name="cs2.db")
        asyncio.run(cs2.bot.open_store(migrate=cs2.init_db))
        self.players = {ID_ANN: ("Ann", 1, ""), ID_BOB: ("Bob", 1, ""), ID_CAL: ("Cal", 0, "")}
        self.vanity = {"annvanity": ID_ANN}
        self.urls = []
        cs2.get = self.get

    def get(self, url):
        self.urls.append(url)
        if "ResolveVanityURL" in url:
            name = url.split("vanityurl=")[1]
            found = self.vanity.get(name)
            return 200, json.dumps({"response": {"success": 1, "steamid": found} if found else {"success": 42}}).encode()
        ids = url.split("steamids=")[1].split("&")[0].split("%2C")
        players = [{"steamid": i, "personaname": self.players[i][0], "personastate": self.players[i][1], "gameid": self.players[i][2]} for i in ids if i in self.players]
        return 200, json.dumps({"response": {"players": players}}).encode()

    def say(self, text, user=ANN):
        for command in cs2.bot.commands.values():
            for each in (command, *getattr(command, "subcommands", {}).values()):
                if each.cooldown is not None:
                    each.cooldown._last.clear()
        asyncio.run(cs2.bot.process_message({"id": "m1", "channel_id": "c1", "author_id": user["id"], "content": text, "seq": 1}))
        return self.client.ephemerals[-1]["content"] if self.client.ephemerals else ""

    def link_all(self):
        for person, steam_id in ((ANN, ID_ANN), (BOB, ID_BOB), (CAL, ID_CAL)):
            asyncio.run(cs2.bot.store.run(cs2.link, person["id"], steam_id))

    def poll(self):
        asyncio.run(cs2.poll())


def test_link_by_id_by_profile_link_and_by_vanity_name():
    rig = Rig()
    assert "linked to Ann" in rig.say(f"!cs2 link {ID_ANN}")
    assert "linked to Ann" in rig.say("!cs2 link https://steamcommunity.com/id/annvanity/")
    assert "linked to Bob" in rig.say(f"!cs2 link https://steamcommunity.com/profiles/{ID_BOB}", user=BOB)
    assert "no profile" in rig.say("!cs2 link nobodyhere")
    assert len(asyncio.run(cs2.bot.store.run(cs2.all_players))) == 2


def test_without_an_api_key_every_steam_command_says_so_and_nothing_is_fetched():
    rig = Rig(STEAM_API_KEY="")
    for text in (f"!cs2 link {ID_ANN}", "!cs2 who", "!cs2 lfg"):
        assert "STEAM_API_KEY" in rig.say(text)
    assert not rig.urls
    rig.poll()
    assert not rig.urls


def test_starting_cs2_is_announced_once_and_stopping_re_arms_it():
    rig = Rig()
    rig.link_all()
    rig.poll()
    assert not rig.client.sent
    rig.players[ID_BOB] = ("Bob", 1, steam.CS2_APP_ID)
    rig.poll()
    rig.poll()
    assert [s["content"] for s in rig.client.sent] == ["@bob just started CS2."]
    assert rig.client.sent[0]["channel_id"] == "c-announce"
    rig.players[ID_BOB] = ("Bob", 1, "")
    rig.poll()
    rig.players[ID_BOB] = ("Bob", 1, steam.CS2_APP_ID)
    rig.poll()
    assert len(rig.client.sent) == 2


def test_quiet_keeps_the_link_but_stops_the_announcement():
    rig = Rig()
    rig.link_all()
    assert "off for you" in rig.say("!cs2 quiet", user=BOB)
    rig.players[ID_BOB] = ("Bob", 1, steam.CS2_APP_ID)
    rig.poll()
    assert not rig.client.sent
    assert "on for you" in rig.say("!cs2 quiet", user=BOB)


def test_forget_removes_the_member_and_they_are_no_longer_polled():
    rig = Rig()
    rig.link_all()
    assert "removed" in rig.say("!cs2 forget", user=BOB)
    assert "not linked" in rig.say("!cs2 forget", user=BOB)
    rig.poll()
    assert ID_BOB not in rig.urls[-1]


def test_lfg_pings_only_when_enough_linked_members_are_online_and_free():
    rig = Rig()
    rig.link_all()
    rig.players[ID_BOB] = ("Bob", 1, "570")
    assert "only 1 linked member is online and free" in rig.say("!cs2 lfg")
    assert not rig.client.sent
    rig.players[ID_BOB] = ("Bob", 1, "")
    rig.say("!cs2 lfg tonight @everyone")
    text = rig.client.sent[-1]["content"]
    assert "@ann" in text and "@bob" in text and "@cal" not in text.split("Free now:")[1] and "@everyone" not in text


def test_lfg_pings_the_role_when_one_is_set():
    rig = Rig(LFG_ROLE="cs2-crew")
    rig.link_all()
    rig.say("!cs2 lfg")
    assert rig.client.sent[-1]["content"].startswith("@cs2-crew ")


def test_who_lists_linked_members_with_their_state():
    rig = Rig()
    rig.link_all()
    rig.players[ID_BOB] = ("Bob", 1, steam.CS2_APP_ID)
    text = rig.say("!cs2 who")
    assert "@ann: online" in text and "@bob: in CS2" in text and "@cal: offline" in text


def test_a_steam_outage_is_one_readable_line_and_the_poll_survives():
    rig = Rig()
    rig.link_all()

    def down(url):
        raise steam.SteamError("could not reach Steam (TimeoutError)")

    cs2.get = down
    assert "could not reach Steam" in rig.say("!cs2 who")
    rig.poll()


def test_server_status_reads_a_query_and_degrades_without_one():
    rig = Rig()
    assert "CS2_SERVER" in rig.say("!cs2 server")
    cs2.SERVER = "10.0.0.5:27015"
    seen = []
    cs2.query_server = lambda host, port: (seen.append((host, port)), {"name": "Friends", "players": 4, "max_players": 10, "map": "de_dust2"})[1]
    rig.say("!cs2 server")
    assert seen == [("10.0.0.5", 27015)] and "4/10 players on de_dust2" in rig.client.sent[-1]["content"]

    def silent(host, port):
        raise steam.SteamError("the game server did not answer (TimeoutError)")

    cs2.query_server = silent
    assert "did not answer" in rig.say("!cs2 server")
    cs2.query_server = steam.query_server


def test_the_key_never_reaches_a_reply():
    rig = Rig()
    rig.link_all()
    for text in ("!cs2 who", "!cs2 lfg", f"!cs2 link {ID_ANN}"):
        assert "KEY123" not in rig.say(text)


if __name__ == "__main__":
    run_module_tests(globals())
