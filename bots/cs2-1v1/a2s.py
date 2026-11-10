"""The A2S_INFO query over UDP, enough to read the map and the human player count from a running server."""

import socket
import struct

REQUEST = b"\xff\xff\xff\xffTSource Engine Query\x00"
TIMEOUT_SECONDS = 3


class QueryError(Exception):
    """The server did not answer, or answered something that is not an A2S_INFO reply."""


def _cstring(data, at):
    end = data.index(b"\x00", at)
    return data[at:end].decode("utf-8", "replace"), end + 1


def parse_info(packet):
    """{map, players, max_players, bots} from an A2S_INFO reply."""
    if packet[:5] != b"\xff\xff\xff\xffI":
        raise QueryError("not a Source query reply")
    try:
        _name, at = _cstring(packet, 6)
        game_map, at = _cstring(packet, at)
        _folder, at = _cstring(packet, at)
        _game, at = _cstring(packet, at)
        players, max_players, bots = struct.unpack_from("BBB", packet, at + 2)
    except (ValueError, struct.error):
        raise QueryError("the reply was cut short") from None
    return {"map": game_map, "players": players, "max_players": max_players, "bots": bots}


def query(host, port, timeout=TIMEOUT_SECONDS):
    """Blocking A2S_INFO, answering the challenge a current server asks for first."""
    target = (host, port)
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout)
            sock.sendto(REQUEST, target)
            packet, _ = sock.recvfrom(4096)
            if packet[4:5] == b"A":
                sock.sendto(REQUEST + packet[5:9], target)
                packet, _ = sock.recvfrom(4096)
    except OSError as err:
        raise QueryError(f"no answer ({type(err).__name__})") from None
    return parse_info(packet)
