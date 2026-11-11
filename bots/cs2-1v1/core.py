"""bot-cs2-1v1's settings, the container spec, log progress parsing and the idle/match-over decision; see README.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" in docs/framework.md."""

import random
import re
import secrets
import string
from dataclasses import dataclass

IMAGE_DEFAULT = "joedwards32/cs2@sha256:b902f6f434204b0c8749a1dcc6e6242159a90fd71411f6e61431a1d687ca496d"
INSTALL_DIR = "/home/steam/cs2-dedicated"
PASSWORD_LENGTH = 10
PROGRESS_POST_SECONDS = 300
LOST_AFTER_SECONDS = 120

# The values reach the image's entrypoint through `eval`, so they must stay inside this alphabet.
_SAFE = re.compile(r"^[A-Za-z0-9_]+$")
_WORKSHOP = re.compile(r"^workshop:(\d{1,20})$")
_DOWNLOAD = re.compile(r"downloading, progress: (\d+(?:\.\d+)?)")

CS2_HOST = ""
CS2_PORT = 27015
CS2_GSLT = ""
CS2_MAX_SERVERS = 1
CS2_IDLE_SECONDS = 600
CS2_EMPTY_SECONDS = 60
CS2_MAX_MATCH_SECONDS = 5400
CS2_START_TIMEOUT_SECONDS = 14400
CS2_MAPS = ["de_dust2", "de_mirage", "de_inferno", "de_nuke"]
CS2_CHANNEL = ""
CS2_PRIVATE = False
CS2_DOCKER_HOST = "http://dockerproxy:2375"
CS2_DOCKER_NETWORK = ""
CS2_IMAGE = IMAGE_DEFAULT
CS2_VOLUME = "cs2-server"
CS2_CPUS = 2.0
CS2_MEMORY_MB = 6144
CS2_RULES = "+mp_maxrounds 6 +mp_warmuptime 30 +mp_autoteambalance 0 +mp_limitteams 0 +mp_halftime 0"


@dataclass
class Server:
    """One running 1v1 container and what the watcher has learned about it."""

    container_id: str
    name: str
    port: int
    owner_id: str
    owner_name: str
    channel_id: str
    map_name: str
    password: str
    started_at: float
    ready_at: float | None = None
    ever_occupied: bool = False
    empty_since: float | None = None
    silent_since: float | None = None
    last_progress_at: float = 0.0
    last_progress_pct: float = -1.0


def configure(bot):
    """Reads every CS2_* variable through the framework's setting() so a bad value is reported at start."""
    global CS2_HOST, CS2_PORT, CS2_GSLT, CS2_MAX_SERVERS, CS2_IDLE_SECONDS, CS2_EMPTY_SECONDS, CS2_MAX_MATCH_SECONDS
    global CS2_START_TIMEOUT_SECONDS, CS2_MAPS, CS2_CHANNEL, CS2_PRIVATE, CS2_DOCKER_HOST, CS2_DOCKER_NETWORK
    global CS2_IMAGE, CS2_VOLUME, CS2_CPUS, CS2_MEMORY_MB, CS2_RULES
    CS2_HOST = bot.setting("CS2_HOST", "", required=True)
    CS2_PORT = bot.setting("CS2_PORT", 27015, type=int)
    CS2_GSLT = bot.setting("CS2_GSLT", "")
    CS2_MAX_SERVERS = max(1, bot.setting("CS2_MAX_SERVERS", 1, type=int))
    CS2_IDLE_SECONDS = 60 * max(1, bot.setting("CS2_IDLE_MINUTES", 10, type=int))
    CS2_EMPTY_SECONDS = max(10, bot.setting("CS2_EMPTY_SECONDS", 60, type=int))
    CS2_MAX_MATCH_SECONDS = 60 * max(5, bot.setting("CS2_MAX_MATCH_MINUTES", 90, type=int))
    CS2_START_TIMEOUT_SECONDS = 60 * max(5, bot.setting("CS2_START_TIMEOUT_MINUTES", 240, type=int))
    CS2_MAPS = [name.strip() for name in bot.setting("CS2_MAPS", CS2_MAPS, type=list) if name.strip()]
    CS2_CHANNEL = bot.setting("CS2_CHANNEL", "")
    CS2_PRIVATE = bot.setting("CS2_PRIVATE", False, type=bool)
    CS2_DOCKER_HOST = bot.setting("CS2_DOCKER_HOST", CS2_DOCKER_HOST)
    CS2_DOCKER_NETWORK = bot.setting("CS2_DOCKER_NETWORK", "", required=True)
    CS2_IMAGE = bot.setting("CS2_IMAGE", IMAGE_DEFAULT)
    CS2_VOLUME = bot.setting("CS2_VOLUME", "cs2-server")
    CS2_CPUS = bot.setting("CS2_CPUS", 2.0, type=float)
    CS2_MEMORY_MB = bot.setting("CS2_MEMORY_MB", 6144, type=int)
    CS2_RULES = bot.setting("CS2_RULES", CS2_RULES)


def map_problem(name):
    return None if _SAFE.match(name) or _WORKSHOP.match(name) else f"CS2_MAPS entry {name!r} is not a map name or workshop:<id>"


def check_config():
    """A startup problem in the settings as a message, or None."""
    for name in CS2_MAPS:
        problem = map_problem(name)
        if problem:
            return problem
    if CS2_GSLT and not _SAFE.match(CS2_GSLT):
        return "CS2_GSLT must be letters and digits only"
    if not CS2_MAPS:
        return "CS2_MAPS must name at least one map"
    if not CS2_DOCKER_HOST.startswith(("http://", "https://")):
        return "CS2_DOCKER_HOST must be an http(s) URL of the socket proxy"
    return None


def choose_map(requested, pick=random.choice):
    """The configured map `requested` names (case-insensitive, `de_` optional), a random one when empty, else None."""
    if not requested:
        return pick(CS2_MAPS)
    wanted = requested.strip().lower()
    for name in CS2_MAPS:
        if wanted in (name.lower(), name.lower().removeprefix("de_")):
            return name
    return None


def new_password(pick=secrets.choice):
    return "".join(pick(string.ascii_lowercase + string.digits) for _ in range(PASSWORD_LENGTH))


def free_port(taken):
    """The first port from CS2_PORT up that no live server uses."""
    port = CS2_PORT
    while port in taken:
        port += 1
    return port


def container_spec(*, map_name, password, port, owner_id, owner_name, channel_id):
    """The create body: the pinned image, the shared install volume, one UDP port and the bot's own labels."""
    env = {
        "CS2_SERVERNAME": "slim-m 1v1", "CS2_PORT": str(port), "CS2_MAXPLAYERS": "2", "CS2_PW": password,
        "CS2_GAMETYPE": "0", "CS2_GAMEMODE": "1", "CS2_CHEATS": "0", "CS2_RCONPW": new_password(),
        "CS2_ADDITIONAL_ARGS": CS2_RULES, "CS2_SERVER_HIBERNATE": "0",
    }
    workshop = _WORKSHOP.match(map_name)
    if workshop:
        env["CS2_HOST_WORKSHOP_MAP"] = workshop.group(1)
    else:
        env["CS2_STARTMAP"] = map_name
    if CS2_GSLT:
        env["SRCDS_TOKEN"] = CS2_GSLT
    key = f"{port}/udp"
    return {
        "Image": CS2_IMAGE,
        "Env": [f"{k}={v}" for k, v in env.items()],
        "ExposedPorts": {key: {}},
        "Labels": {"slimm.owner": owner_id, "slimm.owner_name": owner_name, "slimm.channel": channel_id, "slimm.map": map_name, "slimm.port": str(port)},
        "HostConfig": {
            "PortBindings": {key: [{"HostPort": str(port)}]},
            "Binds": [f"{CS2_VOLUME}:{INSTALL_DIR}"],
            "AutoRemove": True,
            "RestartPolicy": {"Name": "no"},
            "NetworkMode": CS2_DOCKER_NETWORK,
            "Memory": CS2_MEMORY_MB * 1024 * 1024,
            "NanoCpus": int(CS2_CPUS * 1e9),
        },
    }


def server_from_inspect(data, now):
    """A Server rebuilt from an owned container's labels and env after a bot restart."""
    labels = data.get("Config", {}).get("Labels", {})
    env = dict(item.split("=", 1) for item in data.get("Config", {}).get("Env", []) if "=" in item)
    return Server(
        container_id=data["Id"], name=data.get("Name", "").lstrip("/"), port=int(labels.get("slimm.port", CS2_PORT)),
        owner_id=labels.get("slimm.owner", ""), owner_name=labels.get("slimm.owner_name", ""), channel_id=labels.get("slimm.channel", ""),
        map_name=labels.get("slimm.map", ""), password=env.get("CS2_PW", ""), started_at=now,
    )


def download_progress(log_text):
    """The latest steamcmd download percentage in the log tail, or None."""
    found = _DOWNLOAD.findall(log_text)
    return float(found[-1]) if found else None


def connect_line(server):
    return f"connect {CS2_HOST}:{server.port}; password {server.password}"


def decide(server, info, now):
    """What the watcher should do for one server this tick: None, "ready", or ("stop", reason)."""
    if info is None:
        return _decide_silent(server, now)
    server.silent_since = None
    first = server.ready_at is None
    if first:
        server.ready_at = now
    if info["players"] - info["bots"] > 0:
        server.ever_occupied = True
        server.empty_since = None
    elif server.empty_since is None:
        server.empty_since = now
    if now - server.ready_at >= CS2_MAX_MATCH_SECONDS:
        return ("stop", "the match time limit was reached")
    if server.ever_occupied and server.empty_since is not None and now - server.empty_since >= CS2_EMPTY_SECONDS:
        return ("stop", "everyone left, so the match is over")
    if not server.ever_occupied and now - server.ready_at >= CS2_IDLE_SECONDS:
        return ("stop", f"nobody connected for {CS2_IDLE_SECONDS // 60} minutes")
    return "ready" if first else None


def _decide_silent(server, now):
    if server.ready_at is None:
        if now - server.started_at >= CS2_START_TIMEOUT_SECONDS:
            return ("stop", "it never came up")
        return None
    if server.silent_since is None:
        server.silent_since = now
    if now - server.silent_since >= LOST_AFTER_SECONDS:
        return ("stop", "it stopped answering")
    return None
