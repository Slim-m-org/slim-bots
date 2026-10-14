#!/usr/bin/env python3
"""Protocol tests: pure parsers plus real loopback servers for the socket paths; run directly: python3 test_protocols.py."""

import asyncio
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import queries  # noqa: E402
from queries import ProtocolError, Status  # noqa: E402

HEADER = b"\xff\xff\xff\xff"


def a2s_info(name="Valheim", map_name="World", game="Valheim", players=3, max_players=10):
    body = b"\x11" + b"".join(s.encode() + b"\x00" for s in (name, map_name, "valheim", game))
    return HEADER + b"I" + body + struct.pack("<hBBB", 2457, players, max_players, 0) + b"d\x6c\x00\x01"


def mc_response(payload):
    raw = json.dumps(payload).encode()
    body = queries.encode_varint(0) + queries.encode_varint(len(raw)) + raw
    return queries.encode_varint(len(body)) + body


def test_a2s_info_parses_players_map_and_game():
    status = queries.parse_a2s_info(a2s_info())
    assert (status.up, status.players, status.max_players, status.map, status.detail) == (True, 3, 10, "World", "Valheim")


def test_a2s_challenge_is_recognised_and_info_is_not_one():
    assert queries.a2s_challenge(HEADER + b"A" + b"\x01\x02\x03\x04") == b"\x01\x02\x03\x04"
    assert queries.a2s_challenge(a2s_info()) is None


def test_a2s_rejects_truncated_and_foreign_packets():
    for packet in (b"", b"hello world!", a2s_info()[:12], HEADER + b"A" + b"\x00" * 4):
        try:
            queries.parse_a2s_info(packet)
        except ProtocolError:
            continue
        raise AssertionError(f"accepted {packet!r}")


def test_varint_round_trips_including_multibyte_values():
    for value in (0, 1, 127, 128, 300, 25565, 2097151):
        assert queries.decode_varint(queries.encode_varint(value)) == (value, len(queries.encode_varint(value)))
    assert queries.encode_varint(300) == b"\xac\x02"


def test_varint_rejects_truncated_and_overlong_input():
    for data in (b"\x80", b"\xff\xff\xff\xff\xff\xff"):
        try:
            queries.decode_varint(data)
        except ProtocolError:
            continue
        raise AssertionError("accepted a bad VarInt")


def test_minecraft_status_parses_counts_version_and_motd():
    packet = mc_response({"version": {"name": "1.21"}, "players": {"online": 2, "max": 20}, "description": {"text": "Hi", "extra": [{"text": " there"}]}})
    length, offset = queries.decode_varint(packet)
    packet_id, offset = queries.decode_varint(packet, offset)
    status = queries.parse_mc_status(packet[offset:])
    assert (status.players, status.max_players, status.detail) == (2, 20, "1.21 - Hi there")


def test_minecraft_status_tolerates_a_plain_string_motd_and_missing_players():
    body = queries.encode_varint(len(b'{"description": "A"}')) + b'{"description": "A"}'
    status = queries.parse_mc_status(body)
    assert (status.up, status.players, status.detail) == (True, None, "A")


def test_minecraft_status_rejects_bad_json():
    try:
        queries.parse_mc_status(queries.encode_varint(3) + b"{{{")
    except ProtocolError:
        return
    raise AssertionError("accepted bad JSON")


def test_handshake_frames_host_port_and_status_state():
    data = queries.mc_handshake("mc.example", 25565)
    length, offset = queries.decode_varint(data)
    assert data[offset] == 0 and data.endswith(b"\x01\x00")
    assert b"mc.example" in data and struct.pack(">H", 25565) in data


async def serve_tcp(handler):
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def test_minecraft_query_against_a_loopback_server():
    async def run():
        async def handler(reader, writer):
            await reader.read(64)
            writer.write(mc_response({"players": {"online": 1, "max": 4}, "version": {"name": "x"}}))
            await writer.drain()
            writer.close()

        server, port = await serve_tcp(handler)
        async with server:
            return await queries.check("minecraft", "127.0.0.1", port, 2)

    status = asyncio.run(run())
    assert (status.up, status.players, status.max_players) == (True, 1, 4)


def test_a2s_query_follows_the_challenge_over_udp():
    class Server(asyncio.DatagramProtocol):
        def connection_made(self, transport):
            self.transport = transport

        def datagram_received(self, data, addr):
            self.transport.sendto(a2s_info() if data.endswith(b"\xde\xad\xbe\xef") else HEADER + b"A\xde\xad\xbe\xef", addr)

    async def run():
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(Server, local_addr=("127.0.0.1", 0))
        try:
            return await queries.check("a2s", "127.0.0.1", transport.get_extra_info("sockname")[1], 2)
        finally:
            transport.close()

    status = asyncio.run(run())
    assert (status.up, status.players, status.map) == (True, 3, "World")


def test_a_silent_udp_server_times_out_as_down():
    class Silent(asyncio.DatagramProtocol):
        pass

    async def run():
        transport, _ = await asyncio.get_running_loop().create_datagram_endpoint(Silent, local_addr=("127.0.0.1", 0))
        try:
            return await queries.check("a2s", "127.0.0.1", transport.get_extra_info("sockname")[1], 0.2)
        finally:
            transport.close()

    status = asyncio.run(run())
    assert (status.up, status.error) == (False, "timed out")


def test_a_hung_tcp_status_server_times_out_and_garbage_is_down():
    async def run():
        async def hang(reader, writer):
            # Closed in the end: since Python 3.12 leaving `async with server` waits for every connection.
            try:
                await asyncio.sleep(1)
            finally:
                writer.close()

        async def garbage(reader, writer):
            writer.write(b"\x05hello")
            await writer.drain()
            writer.close()

        hung, hung_port = await serve_tcp(hang)
        bad, bad_port = await serve_tcp(garbage)
        async with hung, bad:
            return await queries.check("minecraft", "127.0.0.1", hung_port, 0.2), await queries.check("minecraft", "127.0.0.1", bad_port, 1)

    hung, bad = asyncio.run(run())
    assert hung.error == "timed out" and not bad.up


def test_tcp_check_is_up_when_it_connects_and_down_when_refused():
    async def run():
        server, port = await serve_tcp(lambda r, w: w.close())
        async with server:
            up = await queries.check("tcp", "127.0.0.1", port, 1)
        return up, await queries.check("tcp", "127.0.0.1", port, 1)

    up, refused = asyncio.run(run())
    assert up == Status(up=True) and refused.error == "connection refused"


def test_an_unresolvable_host_is_down_not_an_exception():
    status = asyncio.run(queries.check("tcp", "nonexistent.invalid", 1, 3))
    assert not status.up and status.error


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
