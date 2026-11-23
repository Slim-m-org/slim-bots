"""The websocket URL rule every transport shares: never a token in plaintext outside loopback."""

import os
import sys
import urllib.parse

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")
PROXY_VARIABLES = ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy")


def host_run_server(base: str) -> bool:
    """True inside a host-run connector's sandbox: the host's own `SLIMM_API_URL`, reached only through its egress proxy.
    The server is plain http on a private network there, and the gateway is the only route to it."""
    api = os.environ.get("SLIMM_API_URL", "").rstrip("/")
    if os.environ.get("SLIMM_CONNECTOR") != "1" or not api or base.rstrip("/") != api:
        return False
    return any(os.environ.get(name) for name in PROXY_VARIABLES)


def base_url_problem(base: str) -> str | None:
    """Why a bot token may not travel to `base`, or None: https, http to loopback, or a host-run connector's own server."""
    parts = urllib.parse.urlsplit(base)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS):
        return None
    if parts.scheme == "http" and host_run_server(base):
        return None
    return f"refusing to send a bot token over {parts.scheme or 'no'} scheme to {parts.hostname or base}; use https"


def socket_url(base: str) -> str:
    """The WebSocket URL for `base`, refusing to carry a token in plaintext outside loopback."""
    problem = base_url_problem(base)
    if problem is not None:
        raise RuntimeError(problem)
    parts = urllib.parse.urlsplit(base)
    if parts.scheme == "https":
        return urllib.parse.urlunsplit(("wss", parts.netloc, "/ws", "", ""))
    if not host_run_server(base):
        print("warning: plaintext ws, loopback only", file=sys.stderr)
    return urllib.parse.urlunsplit(("ws", parts.netloc, "/ws", "", ""))
