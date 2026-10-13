"""Jellyfin Quick Connect, run by the bot so a member proves which jellyfin account is theirs without a password; see README.md.
Sync (urllib); call through asyncio.to_thread. The token Jellyfin hands back is logged out at once and never stored."""

from __future__ import annotations

import http.client
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass

import jellyfin_core

CLIENT_NAME = "slim-m"
CLIENT_VERSION = "1.0"
MAX_DEVICE_LABEL = 40


class QuickConnectDisabled(Exception):
    """The jellyfin server has Quick Connect turned off; only its admin can turn it on."""


@dataclass(frozen=True)
class Pending:
    code: str
    secret: str
    device_id: str
    device_name: str


@dataclass(frozen=True)
class Approved:
    user_id: str
    user_name: str


def device_name(member_name):
    """What the approver sees in their jellyfin devices list, so a relayed code names who asked for it."""
    label = re.sub(r"[^A-Za-z0-9 ._-]", "", member_name or "").strip()[:MAX_DEVICE_LABEL] or "a member"
    return f"slim-m link for {label}"


def _client_header(device, device_id, token=None):
    parts = [f'Client="{CLIENT_NAME}"', f'Device="{device}"', f'DeviceId="{device_id}"', f'Version="{CLIENT_VERSION}"']
    if token:
        parts.append(f'Token="{token}"')
    return "MediaBrowser " + ", ".join(parts)


def _call(method, path, header, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(f"{jellyfin_core.JELLYFIN_URL}{path}", data=data, method=method)
    request.add_header("authorization", header)
    request.add_header("user-agent", jellyfin_core.USER_AGENT)
    if data is not None:
        request.add_header("content-type", "application/json")
    with urllib.request.urlopen(request, timeout=20) as response:
        raw = response.read()
    return json.loads(raw) if raw.strip() else None


def initiate(member_name):
    device, device_id = device_name(member_name), uuid.uuid4().hex
    try:
        result = _call("POST", "/QuickConnect/Initiate", _client_header(device, device_id))
    except urllib.error.HTTPError as err:
        if err.code == 401:
            raise QuickConnectDisabled from err
        raise
    return Pending(code=str(result["Code"]), secret=result["Secret"], device_id=device_id, device_name=device)


def is_approved(pending):
    result = _call("GET", f"/QuickConnect/Connect?secret={urllib.parse.quote(pending.secret)}", _client_header(pending.device_name, pending.device_id))
    return bool(result and result.get("Authenticated"))


def redeem(pending):
    """The approving user, read from Jellyfin's answer rather than anything the member typed; the session it opens is closed again."""
    header = _client_header(pending.device_name, pending.device_id)
    result = _call("POST", "/Users/AuthenticateWithQuickConnect", header, {"Secret": pending.secret})
    user = result["User"]
    approved = Approved(user_id=user["Id"], user_name=user["Name"])
    close_session(pending, result["AccessToken"])
    return approved


def close_session(pending, token):
    """Logs the link session out; if that fails the admin key deletes the device, which revokes its token too."""
    try:
        _call("POST", "/Sessions/Logout", _client_header(pending.device_name, pending.device_id, token))
        return
    except (OSError, http.client.HTTPException) as err:
        print(f"jellyfin quick connect: logout failed ({type(err).__name__}), deleting the device instead", file=sys.stderr)
    try:
        _call("DELETE", f"/Devices?id={urllib.parse.quote(pending.device_id)}", jellyfin_core.jellyfin_auth_header())
    except (OSError, http.client.HTTPException) as err:
        print(f"jellyfin quick connect: deleting the link device failed: {type(err).__name__}", file=sys.stderr)
