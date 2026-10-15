"""Jellyfin Quick Connect, run by the bot so a member proves which jellyfin account is theirs without a password; see README.md.
The token Jellyfin hands back is logged out at once and never stored."""

from __future__ import annotations

import re
import sys
import uuid
from dataclasses import dataclass

from slimbots.web import ServiceAuthError, ServiceError

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


async def _call(method, path, header, body=None, params=None):
    return await jellyfin_core.service().request(method, path, body=body, params=params, headers={"authorization": header})


async def initiate(member_name):
    device, device_id = device_name(member_name), uuid.uuid4().hex
    try:
        result = await _call("POST", "/QuickConnect/Initiate", _client_header(device, device_id))
    except ServiceAuthError as err:
        raise QuickConnectDisabled from err
    return Pending(code=str(result["Code"]), secret=result["Secret"], device_id=device_id, device_name=device)


async def is_approved(pending):
    header = _client_header(pending.device_name, pending.device_id)
    result = await _call("GET", "/QuickConnect/Connect", header, params={"secret": pending.secret})
    return bool(result and result.get("Authenticated"))


async def redeem(pending):
    """The approving user, read from Jellyfin's answer rather than anything the member typed; the session it opens is closed again."""
    header = _client_header(pending.device_name, pending.device_id)
    result = await _call("POST", "/Users/AuthenticateWithQuickConnect", header, {"Secret": pending.secret})
    user = result["User"]
    approved = Approved(user_id=user["Id"], user_name=user["Name"])
    await close_session(pending, result["AccessToken"])
    return approved


async def close_session(pending, token):
    """Logs the link session out; if that fails the admin key deletes the device, which revokes its token too."""
    try:
        await _call("POST", "/Sessions/Logout", _client_header(pending.device_name, pending.device_id, token))
        return
    except ServiceError as err:
        print(f"jellyfin quick connect: logout failed ({type(err).__name__}), deleting the device instead", file=sys.stderr)
    try:
        await _call("DELETE", "/Devices", jellyfin_core.jellyfin_auth_header(), params={"id": pending.device_id})
    except ServiceError as err:
        print(f"jellyfin quick connect: deleting the link device failed: {type(err).__name__}", file=sys.stderr)
