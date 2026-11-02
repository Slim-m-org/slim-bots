#!/usr/bin/env python3
"""The Steam readers and the A2S query against a local UDP socket; run directly: python3 test_steam.py."""

import json
import os
import socket
import struct
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import steam  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402


def info_packet(name="Friends CS2", game_map="de_mirage", players=3, maximum=10, bots=1):
    body = b"\x11" + name.encode() + b"\x00" + game_map.encode() + b"\x00csgo\x00Counter-Strike 2\x00" + struct.pack("<HBBB", 730, players, maximum, bots)
    return b"\xff\xff\xff\xffI" + body


def serve(answers):
    """A one-shot UDP server that replies with each of `answers` in turn; returns (port, thread)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    seen = []

    def run():
        for answer in answers:
            data, addr = sock.recvfrom(1024)
            seen.append(data)
            sock.sendto(answer, addr)
        sock.close()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return sock.getsockname()[1], thread, seen


def test_parse_info_reads_name_map_and_counts():
    assert steam.parse_info(info_packet()) == {"name": "Friends CS2", "map": "de_mirage", "players": 3, "max_players": 10, "bots": 1}


def test_a_packet_that_is_not_a_source_answer_or_is_cut_short_is_an_error():
    for packet in (b"garbage", info_packet()[:14]):
        try:
            steam.parse_info(packet)
        except steam.SteamError:
            continue
        raise AssertionError("accepted a bad packet")


def test_query_answers_the_challenge_a_current_server_sends_first():
    port, thread, seen = serve([b"\xff\xff\xff\xffA\x01\x02\x03\x04", info_packet(players=7)])
    info = steam.query_server("127.0.0.1", port, timeout=3)
    thread.join(3)
    assert info["players"] == 7 and seen[1].endswith(b"\x01\x02\x03\x04")


def test_a_silent_server_is_a_readable_error():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("127.0.0.1", 0))
    try:
        steam.query_server("127.0.0.1", sock.getsockname()[1], timeout=0.3)
    except steam.SteamError as err:
        assert "did not answer" in str(err)
    else:
        raise AssertionError("no error")
    finally:
        sock.close()


def test_player_summaries_batches_and_never_puts_the_key_in_an_error():
    urls = []

    def get(url):
        urls.append(url)
        return 403, b""

    try:
        steam.player_summaries(get, "TOPSECRET", ["7656119" + str(i).zfill(10) for i in range(150)])
    except steam.SteamError as err:
        assert "TOPSECRET" not in str(err)
    ids = ["7656119" + str(i).zfill(10) for i in range(150)]
    calls = []
    steam.player_summaries(lambda u: (calls.append(u), (200, json.dumps({"response": {"players": []}}).encode()))[1], "k", ids)
    assert len(calls) == 2


def test_steam_ids_are_recognised():
    assert steam.is_steam_id("76561198000000000") and not steam.is_steam_id("gabelogannewell") and not steam.is_steam_id("12345")


if __name__ == "__main__":
    run_module_tests(globals())
