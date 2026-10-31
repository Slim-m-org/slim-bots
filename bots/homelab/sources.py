"""Homelab readers (Proxmox, Docker, Jellyfin) and the tile text for each; every fetch is an injectable callable."""

import http.client
import json
import socket
import ssl
import urllib.error
import urllib.request

TIMEOUT = 8
MAX_BODY = 4 * 1024 * 1024
MAX_NAMES = 4
MAX_TILE_CHARS = 300
USER_AGENT = "slimm-bot-homelab/1.0"


class SourceError(Exception):
    """A fetch or parse failure; the message is safe to show and never holds a credential."""


def http_get(url, headers, verify=True):
    """One GET returning (status, body); a connection failure is a SourceError naming only the exception type."""
    context = None if verify else ssl._create_unverified_context()
    request = urllib.request.Request(url, headers={"user-agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT, context=context) as response:
            return response.status, response.read(MAX_BODY + 1)[:MAX_BODY]
    except urllib.error.HTTPError as err:
        return err.code, b""
    except Exception as err:
        raise SourceError(f"could not connect ({type(err).__name__})") from err


def json_of(status, body):
    if status != 200:
        raise SourceError(f"http {status}")
    try:
        return json.loads(body)
    except ValueError as err:
        raise SourceError("the answer is not JSON") from err


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=TIMEOUT)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(TIMEOUT)
        self.sock.connect(self._path)


def docker_get(path, route="/containers/json?all=1"):
    """The Docker engine's answer to one read-only GET over its unix socket."""
    conn = _UnixConnection(path)
    try:
        conn.request("GET", route)
        response = conn.getresponse()
        return response.status, response.read(MAX_BODY + 1)[:MAX_BODY]
    except OSError as err:
        raise SourceError(f"could not reach the docker socket ({type(err).__name__})") from err
    finally:
        conn.close()


def read_proxmox(get, url, token):
    base = url.rstrip("/") + "/api2/json/cluster/resources"
    headers = {"authorization": f"PVEAPIToken={token}"}
    data = json_of(*get(base, headers)).get("data")
    if not isinstance(data, list):
        raise SourceError("no resource list in the answer")
    return data


def read_docker(get, path):
    data = json_of(*get(path))
    if not isinstance(data, list):
        raise SourceError("no container list in the answer")
    return data


def read_jellyfin(get, url, key):
    route = url.rstrip("/") + "/Sessions?activeWithinSeconds=60"
    data = json_of(*get(route, {"x-emby-token": key}))
    return [s for s in data if isinstance(s, dict) and s.get("NowPlayingItem")]


def _short(text, limit=28):
    text = str(text or "").replace("\n", " ").replace("@", " at ")
    return text if len(text) <= limit else text[: limit - 1] + "~"


def _pct(value):
    return f"{round(float(value or 0) * 100)}%"


def _names(items):
    shown = ", ".join(_short(n, 20) for n in items[:MAX_NAMES])
    return shown + (f" +{len(items) - MAX_NAMES}" if len(items) > MAX_NAMES else "")


def proxmox_tile(resources):
    lines = ["Proxmox"]
    for node in sorted((r for r in resources if r.get("type") == "node"), key=lambda r: str(r.get("node"))):
        name = _short(node.get("node"), 14)
        if node.get("status") != "online":
            lines.append(f"{name}  DOWN")
            continue
        ram = (node.get("mem") or 0) / node["maxmem"] if node.get("maxmem") else 0
        lines.append(f"{name}  cpu {_pct(node.get('cpu'))}  ram {_pct(ram)}")
    guests = [r for r in resources if r.get("type") in ("qemu", "lxc") and not r.get("template")]
    down = [g.get("name") or f"id {g.get('vmid')}" for g in guests if g.get("status") != "running"]
    lines.append(f"guests: {len(guests) - len(down)} up, {len(down)} down")
    if down:
        lines.append("down: " + _names(down))
    return lines


def docker_tile(containers):
    running = [c for c in containers if c.get("State") == "running"]
    sick = [c for c in running if "unhealthy" in str(c.get("Status", ""))]
    stopped = [c for c in containers if c.get("State") != "running"]
    lines = ["Containers", f"{len(running) - len(sick)} healthy, {len(sick)} unhealthy, {len(stopped)} stopped"]

    def name(c):
        return (c.get("Names") or [c.get("Id", "?")[:12]])[0].lstrip("/")

    if sick:
        lines.append("unhealthy: " + _names([name(c) for c in sick]))
    if stopped:
        lines.append("stopped: " + _names([name(c) for c in stopped]))
    return lines


def jellyfin_tile(sessions):
    if not sessions:
        return ["Jellyfin", "nothing playing"]
    lines = ["Jellyfin", f"{len(sessions)} playing"]
    for s in sessions[:MAX_NAMES]:
        item = s["NowPlayingItem"]
        paused = " (paused)" if (s.get("PlayState") or {}).get("IsPaused") else ""
        lines.append(f"{_short(s.get('UserName'), 12)}: {_short(item.get('Name'), 30)}{paused}")
    return lines


def unreachable_tile(title, err):
    return [title, f"unreachable: {_short(err, 60)}"]


def tile_text(lines):
    return "\n".join(lines)[:MAX_TILE_CHARS]
