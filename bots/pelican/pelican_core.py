"""bot-pelican's settings, Pelican Client API access, and rendering; see docs/framework.md.
Never imports `bot` (the entry point) - see "Splitting a bot across files" there for why."""

import urllib.parse

from slimbots import Permissions
from slimbots.urlpolicy import service_url_problem
from slimbots.web import JsonService, ServiceAuthError, ServiceError, ServiceHTTPError, ServiceUnreachable

USER_AGENT = "slimm-bot-pelican/1.0"
REQUEST_TIMEOUT_SECONDS = 20
PAGE_SIZE = 100
MAX_PAGES = 10
MIN_STATUS_INTERVAL_SECONDS = 30
STATE_MARKERS = {"running": "[up]", "starting": "[starting]", "stopping": "[stopping]", "offline": "[down]"}

PELICAN_URL = ""
PELICAN_API_KEY = ""
PELICAN_ALLOW_HTTP = False
PELICAN_SERVERS = []
PELICAN_CONTROL_ROLE = ""
PELICAN_CONTROL_PERMISSION = "MANAGE_SERVER"
PELICAN_LOG_CHANNEL = ""
PELICAN_STATUS_CHANNEL = ""
PELICAN_STATUS_INTERVAL = 60


class PelicanError(Exception):
    """A panel call that failed; `str(self)` is safe to show in chat and never carries the API key."""


class PelicanAuthError(PelicanError):
    """The panel rejected the API key - terminal for the status loop."""


def url_problem(url, allow_http):
    """Why `url` cannot be the panel address, or None; https is required unless the operator opted into LAN http."""
    return service_url_problem(
        "PELICAN_URL", url, allow_http=allow_http, loopback_http=False, opt_in="PELICAN_ALLOW_HTTP", key_setting="PELICAN_API_KEY",
    )


def permission_problem(name):
    value = getattr(Permissions, name, None)
    if not name.isupper() or not isinstance(value, int) or name == "NONE":
        return f"PELICAN_CONTROL_PERMISSION {name!r} is not a slimbots Permissions name"
    return None


def configure(bot):
    """Resolves every PELICAN_* setting through `bot.setting()`; called once, right after `Bot()` is built."""
    global PELICAN_URL, PELICAN_API_KEY, PELICAN_ALLOW_HTTP, PELICAN_SERVERS, PELICAN_CONTROL_ROLE
    global PELICAN_CONTROL_PERMISSION, PELICAN_LOG_CHANNEL, PELICAN_STATUS_CHANNEL, PELICAN_STATUS_INTERVAL
    PELICAN_URL = (bot.setting("PELICAN_URL", required=True) or "").rstrip("/")
    PELICAN_API_KEY = bot.setting("PELICAN_API_KEY", required=True) or ""
    PELICAN_ALLOW_HTTP = bot.setting("PELICAN_ALLOW_HTTP", False, type=bool)
    PELICAN_SERVERS = [name.lower() for name in bot.setting("PELICAN_SERVERS", [], type=list)]
    PELICAN_CONTROL_ROLE = bot.setting("PELICAN_CONTROL_ROLE", "")
    PELICAN_CONTROL_PERMISSION = bot.setting("PELICAN_CONTROL_PERMISSION", "MANAGE_SERVER")
    PELICAN_LOG_CHANNEL = bot.setting("PELICAN_LOG_CHANNEL", "")
    PELICAN_STATUS_CHANNEL = bot.setting("PELICAN_STATUS_CHANNEL", "")
    PELICAN_STATUS_INTERVAL = max(
        MIN_STATUS_INTERVAL_SECONDS, bot.setting("PELICAN_STATUS_INTERVAL", 60, type=int)
    )


def check_config():
    """A startup problem in the settings as a message, or None."""
    if not PELICAN_URL:
        return None
    return url_problem(PELICAN_URL, PELICAN_ALLOW_HTTP) or permission_problem(PELICAN_CONTROL_PERMISSION)


async def _request(method, path, params=None, body=None):
    # No redirect is followed: one would replay the Authorization header at wherever it points.
    panel = JsonService(
        "the panel", PELICAN_URL, headers={"authorization": f"Bearer {PELICAN_API_KEY}", "accept": "application/json"},
        user_agent=USER_AGENT, timeout=REQUEST_TIMEOUT_SECONDS, max_redirects=0,
    )
    try:
        return await panel.request(method, f"/api/client{path}", params=params or None, body=body)
    except ServiceAuthError as err:
        raise PelicanAuthError(f"the panel refused the API key (HTTP {err.status})") from None
    except ServiceHTTPError as err:
        raise PelicanError(f"the panel answered HTTP {err.status}") from None
    except ServiceUnreachable:
        raise PelicanError("the panel could not be reached") from None
    except ServiceError:
        raise PelicanError("the panel answered with something that is not JSON") from None


async def api_get(path, params=None):
    """One authenticated GET, parsed."""
    return await _request("GET", path, params)


async def api_power(identifier, signal):
    """One power signal."""
    await _request("POST", f"/servers/{urllib.parse.quote(identifier, safe='')}/power", body={"signal": signal})


def is_allowed(server):
    if not PELICAN_SERVERS:
        return True
    return server["name"].lower() in PELICAN_SERVERS or server["identifier"].lower() in PELICAN_SERVERS


async def list_servers():
    """Every server the key can see and the filter allows, following pagination."""
    servers = []
    for page in range(1, MAX_PAGES + 1):
        payload = await api_get("", {"per_page": PAGE_SIZE, "page": page})
        servers.extend(item["attributes"] for item in payload.get("data", []))
        pagination = (payload.get("meta") or {}).get("pagination") or {}
        if page >= pagination.get("total_pages", 1):
            break
    return [server for server in servers if is_allowed(server)]


async def fetch_resources(identifier):
    """The `attributes` of one server's resource utilisation."""
    return (await api_get(f"/servers/{urllib.parse.quote(identifier, safe='')}/resources"))["attributes"]


def find_servers(servers, query):
    """Exact name or identifier first (any case), else every server whose name contains `query`."""
    needle = query.strip().lower()
    exact = [s for s in servers if needle in (s["name"].lower(), s["identifier"].lower())]
    return exact or [s for s in servers if needle in s["name"].lower()]


def may_control(member):
    """The configured role, or the configured permission (administrators always pass)."""
    if PELICAN_CONTROL_ROLE and PELICAN_CONTROL_ROLE in member.role_ids:
        return True
    return member.has_permission(getattr(Permissions, PELICAN_CONTROL_PERMISSION))


def control_denied_text():
    role = "the control role or " if PELICAN_CONTROL_ROLE else ""
    return f"power commands need {role}{PELICAN_CONTROL_PERMISSION}, which you don't hold."


def format_bytes(count):
    value = float(count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TiB"


def format_uptime(milliseconds):
    minutes = int(milliseconds // 60000)
    days, minutes = divmod(minutes, 1440)
    hours, minutes = divmod(minutes, 60)
    return f"{days}d {hours}h" if days else f"{hours}h {minutes}m"


def memory_text(server, resources):
    used = format_bytes(resources.get("memory_bytes", 0))
    limit_mb = (server.get("limits") or {}).get("memory") or 0
    return f"{used} / {limit_mb} MiB" if limit_mb else f"{used} (no limit)"


def state_of(server, stats):
    if server.get("is_suspended"):
        return "suspended"
    return (stats or {}).get("current_state", "unknown")


def summary_line(server, stats):
    """One line for `!servers` and the status message; a stopped server shows only its state."""
    state = state_of(server, stats)
    line = f"{STATE_MARKERS.get(state, '[?]')} **{server['name']}** - {state}"
    resources = (stats or {}).get("resources")
    if resources and state != "offline":
        line += f" - cpu {resources.get('cpu_absolute', 0):.0f}%, ram {memory_text(server, resources)}"
    return line


def detail_text(server, stats):
    lines = [summary_line(server, stats), f"id `{server['identifier']}` on node {server.get('node', '?')}"]
    resources = (stats or {}).get("resources")
    if resources:
        lines.append(f"disk {format_bytes(resources.get('disk_bytes', 0))}, uptime {format_uptime(resources.get('uptime', 0))}")
        lines.append(
            f"network in {format_bytes(resources.get('network_rx_bytes', 0))}, out {format_bytes(resources.get('network_tx_bytes', 0))}"
        )
    return "\n".join(lines)
