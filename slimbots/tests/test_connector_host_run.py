"""A host-run connector reaches slim-m only through its egress proxy: REST and the websocket both honour it, and the plain-http server is allowed there."""

import asyncio
import json

import httpx
import pytest
import websockets

from slimbots import Bot
from slimbots.client import base_url_problem, socket_url
from slimbots.connector import fetch_settings
from slimbots.gateway import Gateway
from slimbots.http import AsyncClient

API = "http://server:8080"
PROXY_NAMES = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "NO_PROXY", "no_proxy", "ALL_PROXY", "all_proxy")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in (*PROXY_NAMES, "SLIMM_API_URL", "SLIMM_CONNECTOR"):
        monkeypatch.delenv(name, raising=False)


def host_run_env(monkeypatch, proxy="http://gateway:3128"):
    monkeypatch.setenv("SLIMM_CONNECTOR", "1")
    monkeypatch.setenv("SLIMM_API_URL", API)
    monkeypatch.setenv("HTTP_PROXY", proxy)
    monkeypatch.setenv("http_proxy", proxy)


def test_plain_http_to_a_server_is_refused_outside_the_sandbox():
    assert base_url_problem(API) is not None


def test_the_sandbox_servers_plain_http_is_allowed(monkeypatch):
    host_run_env(monkeypatch)
    assert base_url_problem(API) is None
    assert base_url_problem(API + "/") is None
    assert socket_url(API) == "ws://server:8080/ws"


@pytest.mark.parametrize("missing", [("SLIMM_CONNECTOR",), ("SLIMM_API_URL",), ("HTTP_PROXY", "http_proxy")])
def test_each_part_of_the_sandbox_shape_is_needed(monkeypatch, missing):
    host_run_env(monkeypatch)
    for name in missing:
        monkeypatch.delenv(name)
    assert base_url_problem(API) is not None


def test_only_the_hosts_own_api_address_is_allowed(monkeypatch):
    host_run_env(monkeypatch)
    assert base_url_problem("http://elsewhere:8080") is not None
    assert base_url_problem("http://server:9090") is not None


def test_a_missing_https_proxy_alone_does_not_unlock_plain_http(monkeypatch):
    monkeypatch.setenv("SLIMM_CONNECTOR", "1")
    monkeypatch.setenv("SLIMM_API_URL", API)
    assert base_url_problem(API) is not None


class Proxy:
    """An HTTP proxy that records each request line and plays the server: a canned JSON answer, or a tunnel to a local websocket server."""

    def __init__(self, tunnel_to):
        self.lines = []
        self.headers = []
        self._tunnel_to = tunnel_to
        self._server = None

    async def start(self):
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def stop(self):
        self._server.close()
        await self._server.wait_closed()

    async def _serve(self, reader, writer):
        head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
        request_line, *header_lines = head.split("\r\n")
        self.lines.append(request_line)
        self.headers.append({k.lower(): v for k, _, v in (h.partition(": ") for h in header_lines if h)})
        if request_line.startswith("CONNECT"):
            await self._tunnel(reader, writer)
        else:
            body = json.dumps({"id": "bot-1", "username": "pelican", "settings": {"pelican_url": "https://panel.example"}}).encode()
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                         + f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
            await writer.drain()
        writer.close()

    async def _tunnel(self, reader, writer):
        upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", self._tunnel_to)
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()

        async def pipe(source, sink):
            try:
                while data := await source.read(4096):
                    sink.write(data)
                    await sink.drain()
            except (ConnectionError, asyncio.CancelledError):
                pass
            finally:
                sink.close()

        await asyncio.gather(pipe(reader, upstream_writer), pipe(upstream_reader, writer))


async def hello_server():
    async def handle(socket):
        first = json.loads(await socket.recv())
        await socket.send(json.dumps({"type": "hello", "protocol": 1, "saw_ticket": first.get("ticket")}))
        await socket.wait_closed()

    server = await websockets.serve(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


@pytest.mark.parametrize("names", [("HTTP_PROXY",), ("http_proxy",)])
async def test_rest_goes_through_the_proxy_in_absolute_form(monkeypatch, names):
    proxy = Proxy(0)
    address = await proxy.start()
    monkeypatch.setenv("SLIMM_CONNECTOR", "1")
    monkeypatch.setenv("SLIMM_API_URL", API)
    for name in names:
        monkeypatch.setenv(name, address)
    try:
        async with AsyncClient(API, "slimbot_x", "ua") as client:
            assert (await client.me())["id"] == "bot-1"
    finally:
        await proxy.stop()
    assert proxy.lines == ["GET http://server:8080/me HTTP/1.1"]
    assert proxy.headers[0]["authorization"] == "Bearer slimbot_x"


async def test_the_connector_settings_read_goes_through_the_proxy(monkeypatch):
    proxy = Proxy(0)
    address = await proxy.start()
    host_run_env(monkeypatch, address)
    try:
        settings = await asyncio.to_thread(fetch_settings, API, "slimbot_x")
    finally:
        await proxy.stop()
    assert settings == {"pelican_url": "https://panel.example"}
    assert proxy.lines == ["GET http://server:8080/connectors/me HTTP/1.1"]


@pytest.mark.parametrize("name", ["HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy"])
async def test_the_websocket_goes_through_the_proxy_with_a_connect_tunnel(monkeypatch, name):
    server, port = await hello_server()
    proxy = Proxy(port)
    address = await proxy.start()
    monkeypatch.setenv("SLIMM_CONNECTOR", "1")
    monkeypatch.setenv("SLIMM_API_URL", API)
    monkeypatch.setenv(name, address)

    class Client(AsyncClient):
        async def ws_ticket(self):
            return "t-1"

    try:
        async with Client(API, "slimbot_x", "ua") as client, await Gateway.open(client) as gateway:
            assert gateway.hello["saw_ticket"] == "t-1"
    finally:
        await proxy.stop()
        server.close()
        await server.wait_closed()
    assert proxy.lines == ["CONNECT server:8080 HTTP/1.1"]


async def test_the_bot_reads_its_environment_and_starts_through_the_proxy(monkeypatch):
    proxy = Proxy(0)
    address = await proxy.start()
    host_run_env(monkeypatch, address)
    monkeypatch.setenv("SLIMM_URL", API)
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_x")
    monkeypatch.setenv("SLIMM_TOKEN", "slimbot_x")
    bot = Bot()
    try:
        assert bot.connector is True
        assert await asyncio.to_thread(bot.setting, "PELICAN_URL") == "https://panel.example"
        assert bot._resolve_config(None, None) == (API, "slimbot_x")
    finally:
        await proxy.stop()


def test_the_bot_still_refuses_plain_http_in_connector_mode_without_the_sandbox(monkeypatch):
    monkeypatch.setenv("SLIMM_CONNECTOR", "1")
    monkeypatch.setenv("SLIMM_URL", API)
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_x")
    with pytest.raises(RuntimeError, match="refusing to send a bot token"):
        Bot()._resolve_config(None, None)
