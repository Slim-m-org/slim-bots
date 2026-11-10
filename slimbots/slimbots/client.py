"""The websocket URL rule every transport shares: never a token in plaintext outside loopback."""

import sys
import urllib.parse

LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "::1")


def base_url_problem(base: str) -> str | None:
    """Why a bot token may not travel to `base`, or None: only https, or http to loopback."""
    parts = urllib.parse.urlsplit(base)
    if parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS):
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
    print("warning: plaintext ws, loopback only", file=sys.stderr)
    return urllib.parse.urlunsplit(("ws", parts.netloc, "/ws", "", ""))
