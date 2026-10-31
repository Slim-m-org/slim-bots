"""Steam Web API presence, vanity lookup and the A2S_INFO game server query; each network call is injectable."""

import json
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.steampowered.com"
CS2_APP_ID = "730"
TIMEOUT = 10
MAX_IDS_PER_CALL = 100
A2S_REQUEST = b"\xff\xff\xff\xffTSource Engine Query\x00"


class SteamError(Exception):
    """A failure whose message is safe to show: it never carries the API key or a URL."""


def http_get(url):
    """One GET returning (status, body); a connection failure is a SteamError naming only the exception type."""
    request = urllib.request.Request(url, headers={"user-agent": "slimm-bot-cs2/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status, response.read(2 * 1024 * 1024)
    except urllib.error.HTTPError as err:
        return err.code, b""
    except Exception as err:
        raise SteamError(f"could not reach Steam ({type(err).__name__})") from err


def _json(get, route, key, **params):
    url = f"{API}{route}?" + urllib.parse.urlencode({"key": key, **params})
    status, body = get(url)
    if status in (401, 403):
        raise SteamError("Steam refused the API key")
    if status != 200:
        raise SteamError(f"Steam answered http {status}")
    try:
        return json.loads(body).get("response") or {}
    except (ValueError, AttributeError) as err:
        raise SteamError("Steam's answer was not JSON") from err


def is_steam_id(text):
    return text.isdigit() and len(text) == 17 and text.startswith("7656119")


def resolve_vanity(get, key, name):
    """The 17-digit id behind a profile name, or None when there is no such profile."""
    answer = _json(get, "/ISteamUser/ResolveVanityURL/v0001/", key, vanityurl=name)
    return answer.get("steamid") if answer.get("success") == 1 else None


def player_summaries(get, key, steam_ids):
    """{steam id: {name, state, game_id}} for ids with a public profile; a private one still answers, with no game."""
    found = {}
    for start in range(0, len(steam_ids), MAX_IDS_PER_CALL):
        batch = steam_ids[start : start + MAX_IDS_PER_CALL]
        for player in _json(get, "/ISteamUser/GetPlayerSummaries/v2/", key, steamids=",".join(batch)).get("players", []):
            found[player["steamid"]] = {
                "name": player.get("personaname", ""), "state": int(player.get("personastate", 0)), "game_id": str(player.get("gameid", "")),
            }
    return found


def _cstring(data, at):
    end = data.index(b"\x00", at)
    return data[at:end].decode("utf-8", "replace"), end + 1


def parse_info(packet):
    """{name, map, players, max_players, bots} from an A2S_INFO answer packet."""
    if packet[:5] != b"\xff\xff\xff\xffI":
        raise SteamError("the server did not answer like a Source game server")
    try:
        name, at = _cstring(packet, 6)
        game_map, at = _cstring(packet, at)
        _folder, at = _cstring(packet, at)
        _game, at = _cstring(packet, at)
        players, max_players, bots = struct.unpack_from("BBB", packet, at + 2)
    except (ValueError, struct.error) as err:
        raise SteamError("the server's answer was cut short") from err
    return {"name": name, "map": game_map, "players": players, "max_players": max_players, "bots": bots}


def query_server(host, port, timeout=TIMEOUT):
    """A2S_INFO over UDP, answering the challenge a current server asks for first."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.sendto(A2S_REQUEST, (host, port))
            packet, _ = sock.recvfrom(4096)
            if packet[4:5] == b"A":
                sock.sendto(A2S_REQUEST + packet[5:9], (host, port))
                packet, _ = sock.recvfrom(4096)
        except OSError as err:
            raise SteamError(f"the game server did not answer ({type(err).__name__})") from err
    return parse_info(packet)
