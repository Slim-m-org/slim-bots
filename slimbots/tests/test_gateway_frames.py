import json

import pytest

from slimbots import gateway as gateway_module
from slimbots.gateway import Gateway


class FakeSocket:
    """A websocket that replays `incoming` as raw frames after the hello, and records what is sent and whether it closed."""

    def __init__(self, hello, incoming=()):
        self._hello = hello
        self._incoming = list(incoming)
        self.sent = []
        self.closed = False

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def recv(self):
        return json.dumps(self._hello)

    async def close(self):
        self.closed = True

    def __aiter__(self):
        return self._frames()

    async def _frames(self):
        for frame in self._incoming:
            yield json.dumps(frame)


class FakeClient:
    user_agent = "test"

    async def ws_ticket(self):
        return "ticket"

    def socket_url(self):
        return "ws://example.invalid/ws"


async def open_gateway(monkeypatch, socket):
    async def connect(url, **_kwargs):
        return socket

    monkeypatch.setattr(gateway_module.websockets, "connect", connect)
    return await Gateway.open(FakeClient())


async def test_open_sends_the_ticket_and_protocol_in_its_hello(monkeypatch):
    socket = FakeSocket({"type": "hello"})
    await open_gateway(monkeypatch, socket)
    assert socket.sent == [{"type": "hello", "ticket": "ticket", "protocol": gateway_module.PROTOCOL}]


async def test_frames_yields_each_server_frame_decoded_in_order(monkeypatch):
    socket = FakeSocket({"type": "hello"}, incoming=[{"type": "a", "n": 1}, {"type": "b", "n": 2}])
    gateway = await open_gateway(monkeypatch, socket)
    assert [frame async for frame in gateway.frames()] == [{"type": "a", "n": 1}, {"type": "b", "n": 2}]


async def test_send_writes_one_json_frame(monkeypatch):
    socket = FakeSocket({"type": "hello"})
    gateway = await open_gateway(monkeypatch, socket)
    await gateway.send({"type": "typing", "channel_id": "c1"})
    assert socket.sent[-1] == {"type": "typing", "channel_id": "c1"}


async def test_a_reply_that_is_not_a_hello_is_refused_and_the_socket_closed(monkeypatch):
    socket = FakeSocket({"type": "error", "message": "protocol"})
    with pytest.raises(RuntimeError, match="expected a hello"):
        await open_gateway(monkeypatch, socket)
    assert socket.closed


async def test_leaving_the_context_closes_the_socket(monkeypatch):
    socket = FakeSocket({"type": "hello"})
    async with await open_gateway(monkeypatch, socket):
        assert not socket.closed
    assert socket.closed
