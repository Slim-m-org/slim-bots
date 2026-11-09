import asyncio
import json

import pytest

from slimbots import gateway as gateway_module
from slimbots.gateway import Gateway


class FakeSocket:
    def __init__(self, hello):
        self._hello = hello
        self.sent = []

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        return json.dumps(self._hello)


class FakeClient:
    user_agent = "test"

    async def ws_ticket(self):
        return "ticket"

    def socket_url(self):
        return "ws://example.invalid/ws"


async def open_with(monkeypatch, hello):
    async def connect(url, **_kwargs):
        return FakeSocket(hello)

    monkeypatch.setattr(gateway_module.websockets, "connect", connect)
    return await Gateway.open(FakeClient())


async def test_open_keeps_the_hello_so_a_bot_can_read_the_moderation_head(monkeypatch):
    gateway = await open_with(monkeypatch, {"type": "hello", "protocol": 1, "moderation_seq": 42})
    assert gateway.hello["moderation_seq"] == 42


async def test_a_hello_without_a_moderation_head_is_still_accepted(monkeypatch):
    gateway = await open_with(monkeypatch, {"type": "hello", "protocol": 1})
    assert gateway.hello.get("moderation_seq") is None


class SilentSocket:
    """Accepts the client hello and never answers, the way a stalled server behaves behind a healthy ping."""

    def __init__(self):
        self.closed = False

    async def send(self, raw):
        return None

    async def recv(self):
        await asyncio.Event().wait()

    async def close(self):
        self.closed = True


async def test_a_server_that_never_answers_the_hello_is_given_up_on_and_the_socket_closed(monkeypatch):
    socket = SilentSocket()

    async def connect(url, **_kwargs):
        return socket

    monkeypatch.setattr(gateway_module.websockets, "connect", connect)
    started = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError):
        await asyncio.wait_for(Gateway.open(FakeClient(), hello_timeout=0.05), timeout=2)
    assert asyncio.get_running_loop().time() - started < 1, "the outer deadline fired, not the hello timeout"
    assert socket.closed


def test_the_default_hello_wait_is_bounded():
    assert 0 < gateway_module.HELLO_TIMEOUT <= 60
