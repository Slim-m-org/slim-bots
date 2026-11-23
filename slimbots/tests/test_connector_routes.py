"""A connector-mode bot calls only routes slim-m's connector table lets a connector's token reach.
`REFUSED` and `NEEDS_VIEW` mirror crates/slimm-server/src/http/connector_gate.rs (add-on API 7)."""

import asyncio
import re

import httpx
import pytest

from slimbots import Bot
from slimbots import bot as bot_module
from slimbots.authors import AuthorFilter
from slimbots.http import AsyncClient
from slimbots.space import Space

BASE = "https://space.example"
REFUSED = re.compile(
    r"^/(auth(?!/ws-ticket)|account|devices|push|notifications|notification-preferences|voice-notifications|favorites|saved|"
    r"blocks|exports|diagnostics|debug-logs|reports|invites|invite|links|read-states|presence)(/|$)|^/me/.+|^/bots/[^/]+/(token|revoke|permissions|prefix)$"
)
NEEDS_VIEW = re.compile(r"^/(members|users|categories|emoji|soundboard|modules|direct-route|space)(/|$)|^/channels$|^/bots/[^/]+/commands$")


class Gate:
    """The server's connector table as a mock transport; `view` is whether `view_channel` was approved."""

    def __init__(self, *, view: bool):
        self.view = view
        self.seen: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        self.seen.append((method, path))
        if REFUSED.search(path):
            return httpx.Response(403, json={"error": "a connector's token cannot use this route"})
        if method == "GET" and NEEDS_VIEW.search(path) and not self.view:
            return httpx.Response(403, json={"error": "forbidden"})
        return httpx.Response(200, json=self.body(method, path))

    @staticmethod
    def body(method: str, path: str):
        if (method, path) == ("GET", "/me"):
            return {"id": "bot-1", "username": "pelican"}
        if (method, path) == ("POST", "/auth/ws-ticket"):
            return {"ticket": "t"}
        if path == "/sync":
            return {"scopes": []}
        if method == "GET" and path in ("/channels", "/members", "/roles"):
            return []
        return {}


class QuietGateway:
    hello: dict = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def frames(self):
        return
        yield


def connector_bot(monkeypatch, gate):
    monkeypatch.setenv("SLIMM_CONNECTOR", "1")
    bot = Bot(channels={"c1"})
    bot.client = AsyncClient(BASE, "slimbot_x", "ua")
    bot.client._http = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(gate))
    bot.space = Space(bot.client)
    bot.authors = AuthorFilter(bot.client, space=bot.space)

    async def open_gateway(client):
        return QuietGateway()

    monkeypatch.setattr(bot_module.Gateway, "open", open_gateway)

    async def no_catch_up():
        return None

    monkeypatch.setattr(bot, "_catch_up", no_catch_up)
    return bot


async def test_the_startup_calls_of_an_approved_connector_are_all_allowed(monkeypatch):
    gate = Gate(view=True)
    bot = connector_bot(monkeypatch, gate)

    @bot.manifest_command("servers")
    async def servers(run):
        return "ok"

    await asyncio.wait_for(bot._connect_once(lambda: None), 2)
    assert gate.seen, "the bot called nothing"
    assert not [call for call in gate.seen if REFUSED.search(call[1])], gate.seen
    assert ("PUT", "/bots/commands") not in gate.seen


async def test_a_connector_without_view_channel_cannot_start_and_is_told_why(monkeypatch, capsys):
    gate = Gate(view=False)
    bot = connector_bot(monkeypatch, gate)
    bot.base_delay = bot.max_delay = 0.01
    task = asyncio.create_task(bot._run_forever())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "the host refused this connector" in capsys.readouterr().err
    assert ("GET", "/channels") in gate.seen


def test_the_table_mirror_refuses_what_the_server_refuses():
    for path in ("/devices", "/presence/me", "/read-states", "/bots/b1/token", "/bots/b1/revoke", "/bots/b1/permissions", "/me/avatar"):
        assert REFUSED.search(path), path
    for path in ("/me", "/auth/ws-ticket", "/bots/commands", "/bots/ui", "/roles", "/sync", "/connectors/me/runs/r1"):
        assert not REFUSED.search(path), path
