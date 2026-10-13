"""bot-game-status's wire protocols: Steam A2S_INFO over UDP, Minecraft Java Server List Ping, and a TCP connect check.
Parsers are pure functions; the query_* coroutines add the socket and always run under a timeout. See README.md."""

import asyncio
import json
import struct
from dataclasses import dataclass

KINDS = ("a2s", "minecraft", "tcp")
A2S_REQUEST = b"\xff\xff\xff\xffTSource Engine Query\x00"
A2S_CHALLENGE = 0x41
A2S_INFO = 0x49
MC_PROTOCOL_VERSION = 47
MAX_MC_PACKET = 1 << 20
MAX_MOTD = 80


class ProtocolError(Exception):
    """The peer answered, but not with something this module understands."""


@dataclass
class Status:
    up: bool
    players: int | None = None
    max_players: int | None = None
    map: str | None = None
    detail: str | None = None
    error: str | None = None


def down(reason):
    return Status(up=False, error=reason)


def read_cstring(data, offset):
    end = data.find(b"\x00", offset)
    if end < 0:
        raise ProtocolError("unterminated string")
    return data[offset:end].decode("utf-8", "replace"), end + 1


def parse_a2s_info(packet):
    """The Status from an A2S_INFO reply; a challenge reply is not one, see `a2s_challenge`."""
    if len(packet) < 6 or packet[:4] != b"\xff\xff\xff\xff":
        raise ProtocolError("not an A2S packet")
    if packet[4] != A2S_INFO:
        raise ProtocolError(f"unexpected A2S header {packet[4]:#x}")
    name, offset = read_cstring(packet, 6)
    map_name, offset = read_cstring(packet, offset)
    _folder, offset = read_cstring(packet, offset)
    game, offset = read_cstring(packet, offset)
    if len(packet) < offset + 5:
        raise ProtocolError("truncated A2S_INFO")
    players, max_players = packet[offset + 2], packet[offset + 3]
    return Status(up=True, players=players, max_players=max_players, map=map_name or None, detail=game or name or None)


def a2s_challenge(packet):
    """The 4-byte challenge in an S2C_CHALLENGE reply, or None when `packet` is something else."""
    if len(packet) >= 9 and packet[:4] == b"\xff\xff\xff\xff" and packet[4] == A2S_CHALLENGE:
        return packet[5:9]
    return None


def encode_varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def decode_varint(data, offset=0):
    """(value, next offset) of the VarInt at `offset`."""
    value = 0
    for i in range(5):
        if offset + i >= len(data):
            raise ProtocolError("truncated VarInt")
        byte = data[offset + i]
        value |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return value, offset + i + 1
    raise ProtocolError("VarInt too long")


def mc_packet(packet_id, payload=b""):
    body = encode_varint(packet_id) + payload
    return encode_varint(len(body)) + body


def mc_handshake(host, port):
    encoded = host.encode("utf-8")
    payload = encode_varint(MC_PROTOCOL_VERSION) + encode_varint(len(encoded)) + encoded + struct.pack(">H", port) + encode_varint(1)
    return mc_packet(0, payload) + mc_packet(0)


def motd_text(description):
    """The plain text of a chat component, which is a string, or a dict with `text` and nested `extra`."""
    if isinstance(description, str):
        return description
    if not isinstance(description, dict):
        return ""
    return str(description.get("text", "")) + "".join(motd_text(part) for part in description.get("extra", []))


def parse_mc_status(body):
    """The Status from the body of a status response packet (packet id already stripped)."""
    length, offset = decode_varint(body)
    try:
        data = json.loads(body[offset:offset + length].decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProtocolError("status JSON did not parse") from None
    if not isinstance(data, dict):
        raise ProtocolError("status JSON is not an object")
    players = data.get("players") if isinstance(data.get("players"), dict) else {}
    version = data.get("version") if isinstance(data.get("version"), dict) else {}
    motd = " ".join(motd_text(data.get("description")).split())[:MAX_MOTD]
    detail = " - ".join(part for part in (str(version.get("name", "")), motd) if part)
    return Status(up=True, players=players.get("online"), max_players=players.get("max"), detail=detail or None)


async def read_varint(reader):
    value = 0
    for i in range(5):
        byte = (await reader.readexactly(1))[0]
        value |= (byte & 0x7F) << (7 * i)
        if not byte & 0x80:
            return value
    raise ProtocolError("VarInt too long")


class _Datagrams(asyncio.DatagramProtocol):
    def __init__(self):
        self.queue = asyncio.Queue()

    def datagram_received(self, data, _addr):
        self.queue.put_nowait(data)

    def error_received(self, exc):
        self.queue.put_nowait(exc)


async def _next_datagram(protocol):
    item = await protocol.queue.get()
    if isinstance(item, Exception):
        raise item
    return item


async def query_a2s(host, port):
    loop = asyncio.get_running_loop()
    transport, protocol = await loop.create_datagram_endpoint(_Datagrams, remote_addr=(host, port))
    try:
        transport.sendto(A2S_REQUEST)
        packet = await _next_datagram(protocol)
        challenge = a2s_challenge(packet)
        if challenge is not None:
            transport.sendto(A2S_REQUEST + challenge)
            packet = await _next_datagram(protocol)
        return parse_a2s_info(packet)
    finally:
        transport.close()


async def query_minecraft(host, port):
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(mc_handshake(host, port))
        await writer.drain()
        length = await read_varint(reader)
        if not 0 < length <= MAX_MC_PACKET:
            raise ProtocolError("bad status packet length")
        packet = await reader.readexactly(length)
        packet_id, offset = decode_varint(packet)
        if packet_id != 0:
            raise ProtocolError(f"unexpected packet id {packet_id}")
        return parse_mc_status(packet[offset:])
    finally:
        writer.close()


async def query_tcp(host, port):
    _reader, writer = await asyncio.open_connection(host, port)
    writer.close()
    return Status(up=True)


QUERIES = {"a2s": query_a2s, "minecraft": query_minecraft, "tcp": query_tcp}


def reason(err):
    if isinstance(err, asyncio.TimeoutError):
        return "timed out"
    if isinstance(err, asyncio.IncompleteReadError):
        return "connection closed early"
    if isinstance(err, ConnectionRefusedError):
        return "connection refused"
    return str(err) or type(err).__name__


async def check(kind, host, port, timeout):
    """Never raises (cancellation aside): any failure, including a hung peer, is a down Status."""
    try:
        return await asyncio.wait_for(QUERIES[kind](host, port), timeout)
    except Exception as err:
        return down(reason(err))
