#!/usr/bin/env python3
"""Tiles, polling and the command against FakeAsyncClient and scripted readers; run directly: python3 test_bot.py."""

import asyncio
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as homelab  # noqa: E402
import sources  # noqa: E402
from slimbots import Canvas  # noqa: E402
from slimbots.http import ApiError  # noqa: E402
from slimbots.permissions import Permissions  # noqa: E402
from slimbots.testing import FakeAsyncClient, human, run_module_tests, wire_bot  # noqa: E402

ADMIN = human("u-admin", "admin", "Admin")
PROXMOX_URL = "https://pve.lan:8006"
DEFAULT_CONFIG = {
    "PROXMOX_URL": PROXMOX_URL, "PROXMOX_TOKEN": "root@pam!bot=SECRET", "DOCKER_SOCKET": "", "JELLYFIN_URL": "", "JELLYFIN_KEY": "",
}
RESOURCES = [
    {"type": "node", "node": "pve", "status": "online", "cpu": 0.12, "mem": 4, "maxmem": 10},
    {"type": "qemu", "name": "nas", "vmid": 100, "status": "running"},
    {"type": "qemu", "name": "win", "vmid": 101, "status": "stopped"},
    {"type": "lxc", "name": "dns", "vmid": 102, "status": "running"},
]


class CanvasClient(FakeAsyncClient):
    """Adds the canvas routes: a placement echoes its id, an edit answers `affected` like the server does."""

    def __init__(self):
        super().__init__(me_id="bot-1")
        self.placed, self.ops, self.gone = [], [], set()

    async def call(self, method, path, body=None, **kw):
        if path.endswith("/canvas/objects") and method == "POST":
            self.placed.append(body)
            return {**body, "seq": len(self.placed)}
        if path.endswith("/canvas/ops"):
            self.ops.append(body)
            return {"affected": 0 if body.get("object_id") in self.gone else 1}
        return await super().call(method, path, body, **kw)


class Rig:
    def __init__(self, **config):
        for name, value in {**DEFAULT_CONFIG, **config}.items():
            setattr(homelab, name, value)
        self.client = CanvasClient()
        self.client.respond("GET", "/channels", [{"id": "c1", "name": "voice", "kind": "voice", "category_id": None, "created_at": 0}])
        self.client.respond("GET", "/categories", [])
        self.client.respond("GET", "/users/u-admin", {**ADMIN, "role_ids": []})
        self.web = {"cluster/resources": (200, json.dumps({"data": RESOURCES}).encode())}
        self.urls, self.headers = [], []
        homelab.bot.channels = None
        wire_bot(homelab.bot, self.client, db_name="homelab.db")
        asyncio.run(homelab.bot.space.refresh_channels())
        asyncio.run(homelab.bot.open_store(migrate=homelab.init_db))
        homelab.get_url = self.get
        homelab.canvas = Canvas(self.client, "c1")
        homelab.canvas_channel_name = "voice"
        homelab.status.clear()

    def get(self, url, headers, cafile=None):
        self.urls.append(url)
        self.headers.append(headers)
        for key, answer in self.web.items():
            if key in url:
                if isinstance(answer, Exception):
                    raise answer
                return answer
        return 404, b""

    def tick(self):
        asyncio.run(homelab.refresh())

    def say(self, text, permissions=Permissions.MANAGE_SERVER):
        member = homelab.bot.space._make_member({**ADMIN, "role_ids": []})
        member._base_permissions = permissions
        homelab.bot.space.members["u-admin"] = member
        asyncio.run(homelab.bot.process_message({"id": f"m{len(self.client.calls)}", "channel_id": "c1", "author_id": "u-admin", "content": text, "seq": 1}))
        return self.client.ephemerals[-1]["content"] if self.client.ephemerals else ""


def test_first_poll_places_one_note_with_the_node_and_guest_counts():
    rig = Rig()
    rig.tick()
    text = rig.client.placed[0]["props"]["text"]
    assert rig.client.placed[0]["kind"] == "note"
    assert "pve  cpu 12%  ram 40%" in text and "guests: 2 up, 1 down" in text and "down: win" in text
    assert rig.headers[0]["authorization"] == "PVEAPIToken=root@pam!bot=SECRET"


def test_an_unchanged_poll_writes_nothing_and_a_change_edits_in_place():
    rig = Rig()
    rig.tick()
    rig.tick()
    assert len(rig.client.placed) == 1 and not rig.client.ops
    RESOURCES_DOWN = [dict(r) for r in RESOURCES]
    RESOURCES_DOWN[2]["status"] = "running"
    rig.web["cluster/resources"] = (200, json.dumps({"data": RESOURCES_DOWN}).encode())
    rig.tick()
    assert len(rig.client.placed) == 1
    assert rig.client.ops[0]["kind"] == "edit" and "guests: 3 up, 0 down" in rig.client.ops[0]["props"]["text"]
    assert rig.client.ops[0]["object_id"] == rig.client.placed[0]["id"]


def test_an_unreachable_source_shows_unreachable_and_recovers():
    rig = Rig()
    rig.web["cluster/resources"] = sources.SourceError("could not connect (TimeoutError)")
    rig.tick()
    assert "unreachable" in rig.client.placed[0]["props"]["text"]
    assert "unreachable" in rig.say("!homelab")
    rig.web["cluster/resources"] = (200, json.dumps({"data": RESOURCES}).encode())
    rig.tick()
    assert "guests:" in rig.client.ops[0]["props"]["text"]


def test_a_tile_someone_removed_is_placed_again():
    rig = Rig()
    rig.tick()
    rig.client.gone.add(rig.client.placed[0]["id"])
    rig.web["cluster/resources"] = (200, json.dumps({"data": RESOURCES[:2]}).encode())
    rig.tick()
    assert len(rig.client.placed) == 2


def test_a_removed_event_forgets_the_tile():
    rig = Rig()
    rig.tick()
    asyncio.run(homelab.on_canvas_objects_removed({"object_ids": [rig.client.placed[0]["id"]]}))
    rig.tick()
    assert len(rig.client.placed) == 2 and not rig.client.ops


def test_containers_and_jellyfin_tiles_sit_side_by_side():
    sock_body = json.dumps([
        {"Names": ["/web"], "State": "running", "Status": "Up 2 hours (unhealthy)"},
        {"Names": ["/db"], "State": "running", "Status": "Up 2 hours"},
        {"Names": ["/old"], "State": "exited", "Status": "Exited"},
    ]).encode()
    rig = Rig(DOCKER_SOCKET="/run/docker.sock", JELLYFIN_URL="http://jf", JELLYFIN_KEY="k")
    homelab.get_docker = lambda path, *a: (200, sock_body)
    rig.web["Sessions"] = (200, json.dumps([{"UserName": "ann", "NowPlayingItem": {"Name": "Alien"}, "PlayState": {"IsPaused": True}}, {"UserName": "x"}]).encode())
    rig.tick()
    texts = [p["props"]["text"] for p in rig.client.placed]
    assert "1 healthy, 1 unhealthy, 1 stopped" in texts[1] and "unhealthy: web" in texts[1] and "stopped: old" in texts[1]
    assert "ann: Alien (paused)" in texts[2] and "1 playing" in texts[2]
    xs = [p["x"] for p in rig.client.placed]
    assert xs[1] - xs[0] == homelab.TILE_W + homelab.GAP and xs[2] - xs[1] == homelab.TILE_W + homelab.GAP


def test_no_source_set_up_says_what_to_set_and_draws_nothing():
    rig = Rig(PROXMOX_URL="", PROXMOX_TOKEN="")
    rig.tick()
    assert not rig.client.placed
    assert "HOMELAB_PROXMOX_URL" in rig.say("!homelab")


def test_a_refused_canvas_write_is_reported_not_raised():
    rig = Rig()
    orig = rig.client.call

    async def refuse(method, path, body=None, **kw):
        if path.endswith("/canvas/objects"):
            raise ApiError(403, {"error": "forbidden"})
        return await orig(method, path, body, **kw)

    rig.client.call = refuse
    rig.tick()
    assert "canvas refused" in rig.say("!homelab")


def test_redraw_removes_the_tiles_and_needs_manage_server():
    rig = Rig()
    rig.tick()
    assert "Manage Server" in rig.say("!homelab redraw", permissions=0)
    rig.say("!homelab redraw")
    assert rig.client.ops[0]["kind"] == "remove" and rig.client.ops[0]["object_ids"] == [rig.client.placed[0]["id"]]
    assert len(rig.client.placed) == 2


def test_the_token_never_reaches_a_reply():
    rig = Rig()
    rig.tick()
    assert "SECRET" not in rig.say("!homelab")


def test_a_non_voice_channel_is_refused_at_connect():
    homelab.CANVAS_CHANNEL = "voice"
    rig = Rig()
    rig.client.respond("GET", "/channels", [{"id": "c1", "name": "voice", "kind": "text", "category_id": None, "created_at": 0}])
    asyncio.run(homelab.bot.space.refresh_channels())
    try:
        homelab.resolve_channel()
    except RuntimeError as err:
        assert "voice channel" in str(err)
    else:
        raise AssertionError("a text channel was accepted")
    homelab.CANVAS_CHANNEL = None


if __name__ == "__main__":
    run_module_tests(globals())
