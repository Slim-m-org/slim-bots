#!/usr/bin/env python3
"""The A2S_INFO parser and a real UDP round trip with a challenge; run directly: python3 test_a2s.py."""

import os
import socket
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import a2s  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402


def reply(players=1, bots=0):
    body = b"I\x11" + b"name\x00" + b"de_dust2\x00" + b"csgo\x00" + b"CS2\x00" + b"\x00\x00" + bytes([players, 2, bots])
    return b"\xff\xff\xff\xff" + body


def test_parse_reads_the_map_and_counts():
    assert a2s.parse_info(reply(2, 1)) == {"map": "de_dust2", "players": 2, "max_players": 2, "bots": 1}


def test_parse_rejects_a_wrong_header_and_a_short_packet():
    for packet in (b"\xff\xff\xff\xffX", reply()[:12]):
        try:
            a2s.parse_info(packet)
        except a2s.QueryError:
            continue
        raise AssertionError("a bad packet was accepted")


def test_query_answers_the_challenge_over_real_udp_and_a_silent_port_is_a_query_error():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))

    def serve():
        data, addr = sock.recvfrom(4096)
        sock.sendto(b"\xff\xff\xff\xffA\x01\x02\x03\x04", addr)
        data, addr = sock.recvfrom(4096)
        assert data.endswith(b"\x01\x02\x03\x04")
        sock.sendto(reply(1), addr)

    thread = threading.Thread(target=serve)
    thread.start()
    assert a2s.query("127.0.0.1", sock.getsockname()[1], timeout=2)["players"] == 1
    thread.join()
    sock.close()
    try:
        a2s.query("127.0.0.1", 9, timeout=0.2)
    except a2s.QueryError:
        return
    raise AssertionError("a silent port should be a QueryError")


if __name__ == "__main__":
    run_module_tests(globals())
