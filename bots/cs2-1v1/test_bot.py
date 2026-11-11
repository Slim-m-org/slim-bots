#!/usr/bin/env python3
"""Commands and the watcher against FakeAsyncClient, a fake Docker and a scripted query; run directly: python3 test_bot.py."""

import asyncio
import os
import sys

os.environ.update({"CS2_HOST": "play.example", "CS2_DOCKER_NETWORK": "cs2net"})
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import a2s  # noqa: E402
import bot as cs2  # noqa: E402
import core  # noqa: E402
from fakes import FakeDocker, info  # noqa: E402
from slimbots.testing import FakeAsyncClient, human, run_module_tests, wire_bot  # noqa: E402

ANN = human("u-ann", "ann", "Ann")
BOB = human("u-bob", "bob", "Bob")
ROOT = human("u-root", "root", "Root", role_ids=["r-admin"])
ROLES = [{"id": "r-everyone", "name": "@everyone", "permissions": 0, "is_everyone": True}, {"id": "r-admin", "name": "admin", "permissions": 1, "is_everyone": False}]


class Rig:
    def __init__(self, **config):
        defaults = {"CS2_HOST": "play.example", "CS2_PORT": 27015, "CS2_MAX_SERVERS": 1, "CS2_IDLE_SECONDS": 600, "CS2_EMPTY_SECONDS": 60,
                    "CS2_MAX_MATCH_SECONDS": 5400, "CS2_START_TIMEOUT_SECONDS": 14400, "CS2_CHANNEL": "", "CS2_PRIVATE": False, "CS2_GSLT": "",
                    "CS2_MAPS": ["de_dust2", "de_mirage"], "CS2_IMAGE": core.IMAGE_DEFAULT}
        for name, value in {**defaults, **config}.items():
            setattr(core, name, value)
        self.now = 1000.0
        self.answer = None
        self.docker = FakeDocker()
        self.docker.images.add(core.IMAGE_DEFAULT)
        cs2.servers.clear()
        cs2.setup_state(self.docker)
        cs2.clock = lambda: self.now
        cs2.query = lambda host, port: self._query(host, port)
        self.queried = []
        self.client = FakeAsyncClient(me_id="bot-1")
        self.client.respond("GET", "/members", [ANN, BOB, ROOT])
        self.client.respond("GET", "/roles", ROLES)
        wire_bot(cs2.bot, self.client, me_id="bot-1")
        asyncio.run(cs2.bot.space.refresh_roles())
        asyncio.run(cs2.bot.space.refresh_members())

    def _query(self, host, port):
        self.queried.append((host, port))
        if self.answer is None:
            raise a2s.QueryError("no answer")
        return self.answer

    def say(self, text, user=ANN, channel="c1"):
        asyncio.run(cs2.bot.process_message({"id": "m1", "channel_id": channel, "author_id": user["id"], "content": text, "seq": 1}))
        sent = self.client.sent[-1]["content"] if self.client.sent else ""
        return sent

    def tick(self, seconds=15, answer="keep"):
        self.now += seconds
        if answer != "keep":
            self.answer = answer
        asyncio.run(cs2.watch_once())

    def posts(self):
        return [p["content"] for p in self.client.sent]

    def running(self):
        return [c for c in self.docker.containers.values() if c["Labels"]]


def test_start_creates_one_pinned_labelled_container_with_a_random_password_and_says_the_first_start_is_slow():
    rig = Rig()
    reply = rig.say("!1v1 mirage")
    assert "starting a 1v1 on `de_mirage`" in reply and "75 GB" in reply
    (_, name, spec) = [c for c in rig.docker.calls if c[0] == "create"][0]
    assert name == "cs2-1v1-27015" and "@sha256:" in spec["Image"]
    env = dict(item.split("=", 1) for item in spec["Env"])
    assert env["CS2_MAXPLAYERS"] == "2" and env["CS2_STARTMAP"] == "de_mirage" and len(env["CS2_PW"]) == core.PASSWORD_LENGTH
    assert env["CS2_BOT_QUOTA"] == "0", "a 1v1 has room for exactly two people, so no CS2 bots"
    assert spec["Labels"]["slimm.owner"] == "u-ann"
    host = spec["HostConfig"]
    assert host["PortBindings"] == {"27015/udp": [{"HostPort": "27015"}]} and host["AutoRemove"] and host["NetworkMode"] == "cs2net"
    assert host["Binds"] == [f"cs2-server:{core.INSTALL_DIR}"] and not host.get("Privileged")


def test_a_missing_image_is_pulled_once_then_created():
    rig = Rig()
    rig.docker.images.clear()
    rig.say("!1v1")
    kinds = [c[0] for c in rig.docker.calls]
    assert kinds == ["create", "pull", "create", "start"]


def test_an_unknown_map_lists_the_configured_ones_and_starts_nothing():
    rig = Rig()
    reply = rig.say("!1v1 ../../etc")
    assert "`de_dust2`" in reply and "`de_mirage`" in reply
    assert not rig.docker.calls


def test_a_workshop_map_sets_the_workshop_variable_instead_of_a_start_map():
    rig = Rig(CS2_MAPS=["workshop:3070284539"])
    rig.say("!1v1")
    env = dict(item.split("=", 1) for item in rig.docker.calls[0][2]["Env"])
    assert env["CS2_HOST_WORKSHOP_MAP"] == "3070284539" and "CS2_STARTMAP" not in env


def test_the_gslt_token_goes_into_the_server_env_only_when_set():
    rig = Rig(CS2_GSLT="ABC123")
    rig.say("!1v1")
    assert "SRCDS_TOKEN=ABC123" in rig.docker.calls[0][2]["Env"]
    rig = Rig()
    rig.say("!1v1")
    assert not any(item.startswith("SRCDS_TOKEN") for item in rig.docker.calls[0][2]["Env"])


def test_the_connect_line_is_posted_when_the_server_answers_and_never_before():
    rig = Rig()
    rig.say("!1v1")
    password = next(iter(cs2.servers.values())).password
    rig.tick()
    assert not any("connect play" in p for p in rig.posts())
    rig.tick(answer=info())
    assert f"connect play.example:27015; password {password}" in rig.posts()[-1]
    assert rig.queried[-1] == ("cs2-1v1-27015", 27015)


def test_private_mode_keeps_the_password_out_of_the_channel_posts():
    rig = Rig(CS2_PRIVATE=True)
    rig.say("!1v1")
    password = next(iter(cs2.servers.values())).password
    rig.tick(answer=info())
    assert all(password not in p for p in rig.posts())
    rig.say("!1v1 status", user=BOB)
    assert all(password not in p for p in rig.posts()) and not rig.client.ephemerals
    rig.say("!1v1 status")
    assert all(password not in p for p in rig.posts())
    assert f"password {password}" in rig.client.ephemerals[-1]["content"]


def test_a_second_start_while_one_runs_names_the_running_server_and_creates_nothing():
    rig = Rig()
    rig.say("!1v1")
    reply = rig.say("!1v1 dust2", user=BOB)
    assert "already running" in reply and "@ann" in reply
    assert len([c for c in rig.docker.calls if c[0] == "create"]) == 1


def test_max_servers_above_one_uses_the_next_port():
    rig = Rig(CS2_MAX_SERVERS=2)
    rig.say("!1v1")
    rig.say("!1v1", user=BOB)
    assert sorted(s.port for s in cs2.servers.values()) == [27015, 27016]


def test_the_starter_can_stop_it():
    rig = Rig()
    rig.say("!1v1")
    reply = rig.say("!1v1 stop")
    assert "stopped by @ann" in reply and not rig.running() and not cs2.servers


def test_an_admin_can_stop_someone_elses_server():
    rig = Rig()
    rig.say("!1v1")
    assert "stopped by @root" in rig.say("!1v1 stop", user=ROOT) and not rig.running()


def test_another_member_cannot_stop_it_and_it_keeps_running():
    rig = Rig()
    rig.say("!1v1")
    assert "only the member who started" in rig.say("!1v1 stop", user=BOB)
    assert len(rig.running()) == 1


def test_stop_with_nothing_running_says_so():
    rig = Rig()
    assert "no 1v1 server" in rig.say("!1v1 stop")


def test_nobody_connecting_for_the_idle_time_stops_it():
    rig = Rig()
    rig.say("!1v1")
    rig.tick(answer=info())
    rig.tick(500)
    assert rig.running()
    rig.tick(101)
    assert not rig.running() and "nobody connected for 10 minutes" in rig.posts()[-1]


def test_the_match_ending_stops_it_once_both_players_have_left():
    rig = Rig()
    rig.say("!1v1")
    rig.tick(answer=info(players=2))
    rig.tick(900)
    rig.tick(15, answer=info(players=0))
    rig.tick(30)
    assert rig.running()
    rig.tick(40)
    assert not rig.running() and "match is over" in rig.posts()[-1]


def test_a_player_dropping_and_rejoining_inside_the_window_keeps_the_server():
    rig = Rig()
    rig.say("!1v1")
    rig.tick(answer=info(players=2))
    rig.tick(15, answer=info(players=1))
    rig.tick(45, answer=info(players=0))
    rig.tick(15, answer=info(players=1))
    rig.tick(300)
    assert rig.running()


def test_bots_do_not_count_as_players():
    rig = Rig()
    rig.say("!1v1")
    rig.tick(answer=info(players=2, bots=2))
    rig.tick(700)
    assert not rig.running()


def test_the_match_time_limit_stops_it_even_with_players_connected():
    rig = Rig(CS2_MAX_MATCH_SECONDS=1800)
    rig.say("!1v1")
    rig.tick(answer=info(players=2))
    rig.tick(1801)
    assert not rig.running() and "time limit" in rig.posts()[-1]


def test_a_server_that_never_comes_up_is_stopped_after_the_start_timeout_and_a_download_is_reported():
    rig = Rig(CS2_START_TIMEOUT_SECONDS=3600)
    rig.say("!1v1")
    rig.docker.logs_text = "Update state (0x61) downloading, progress: 12.50 (7 / 56)\n"
    rig.tick(301)
    assert "downloading the game, 12% done" in rig.posts()[-1]
    rig.tick(301)
    assert sum("downloading" in p for p in rig.posts()) == 1
    rig.tick(3600)
    assert not rig.running() and "never came up" in rig.posts()[-1]


def test_a_server_that_goes_silent_after_being_up_is_stopped():
    rig = Rig()
    rig.say("!1v1")
    rig.tick(answer=info(players=1))
    rig.tick(15, answer=None)
    assert rig.running()
    rig.tick(130)
    assert not rig.running() and "stopped answering" in rig.posts()[-1]


def test_docker_unreachable_gets_a_plain_reply_and_the_watcher_survives():
    rig = Rig()
    rig.docker.down = True
    assert "can't reach Docker" in rig.say("!1v1")
    rig.tick()


def test_foreign_containers_are_never_listed_adopted_or_stopped():
    rig = Rig()
    rig.docker.add_foreign()
    rig.say("!1v1")
    assert "someone-elses" in rig.docker.containers and len(cs2.servers) == 1
    rig.say("!1v1 stop")
    assert "someone-elses" in rig.docker.containers and rig.docker.calls[-1][0] == "stop" and rig.docker.calls[-1][1] != "someone-elses"
    assert asyncio.run(_stop_foreign(rig)) is True


async def _stop_foreign(rig):
    from dockerapi import NotOwned

    try:
        await rig.docker.stop("someone-elses")
    except NotOwned:
        return True
    return False


def test_a_restarted_bot_adopts_its_labelled_container_and_still_enforces_who_may_stop_it():
    rig = Rig()
    rig.say("!1v1")
    password = next(iter(cs2.servers.values())).password
    cs2.servers.clear()
    asyncio.run(cs2.watch_once())
    adopted = next(iter(cs2.servers.values()))
    assert adopted.owner_id == "u-ann" and adopted.password == password and adopted.port == 27015
    assert "only the member who started" in rig.say("!1v1 stop", user=BOB)
    assert "stopped by @ann" in rig.say("!1v1 stop")


def test_a_container_removed_outside_the_bot_is_dropped_and_announced():
    rig = Rig()
    rig.say("!1v1")
    rig.docker.containers.clear()
    rig.tick()
    assert not cs2.servers and "is gone" in rig.posts()[-1]


def test_the_channel_restriction_refuses_other_channels():
    rig = Rig(CS2_CHANNEL="c-games")
    rig.say("!1v1", channel="c-other")
    assert not rig.docker.calls
    rig.say("!1v1", channel="c-games")
    assert rig.docker.calls


def test_status_shows_the_connect_line_once_up():
    rig = Rig()
    assert "no 1v1 server" in rig.say("!1v1 status")
    rig.say("!1v1")
    assert "(starting)" in rig.say("!1v1 status")
    rig.tick(answer=info())
    assert "(up)" in rig.say("!1v1 status") and "connect play.example:27015" in rig.posts()[-1]


def test_help_names_the_commands_and_the_slow_first_start():
    rig = Rig()
    reply = rig.say("!1v1 help")
    assert "1v1 stop" in reply and "first start" in reply


def test_config_refuses_values_that_could_reach_the_entrypoint_shell():
    rig = Rig(CS2_GSLT="abc; rm -rf /")
    assert "CS2_GSLT" in core.check_config()
    rig = Rig(CS2_MAPS=["de_dust2 +rcon_password x"])
    assert "CS2_MAPS" in core.check_config()
    Rig()
    assert core.check_config() is None


if __name__ == "__main__":
    run_module_tests(globals())
