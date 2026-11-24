"""Reads and writes a Jellyfin user's own playback position, so Continue Watching stays right; see README.md.
The API key can act as any user; a linked member uses theirs, anyone else the shared `JELLYFIN_USER_ID`, and with that unset nobody."""

from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

from slimbots.web import ServiceError, ServiceHTTPError

import jellyfin_core

TICKS_PER_SECOND = 10_000_000
MIN_RESUME_SECONDS = 30
NEAR_END_SECONDS = 60
REPORT_EVERY_SECONDS = 30

JELLYFIN_USER_ID = ""

# The shared account that last passed `check_shared_account`, so it is looked up once.
_checked_user_id = None


class SharedAccountError(RuntimeError):
    """`JELLYFIN_USER_ID` would lend members an account that is not theirs to borrow; the message names the setting."""


def configure(bot):
    global JELLYFIN_USER_ID
    JELLYFIN_USER_ID = bot.setting("JELLYFIN_USER_ID", "") or ""


async def check_shared_account():
    """Raises `SharedAccountError` unless `JELLYFIN_USER_ID` is unset or names an enabled, non-administrator user."""
    global _checked_user_id
    if not JELLYFIN_USER_ID or _checked_user_id == JELLYFIN_USER_ID:
        return
    try:
        user = await jellyfin_core.jf_get(f"/Users/{JELLYFIN_USER_ID}")
    except ServiceHTTPError as err:
        if err.status in (400, 404):
            raise SharedAccountError(f"JELLYFIN_USER_ID ({JELLYFIN_USER_ID}) names no jellyfin user.") from err
        raise
    policy = user.get("Policy") or {}
    if policy.get("IsAdministrator"):
        raise SharedAccountError(
            "JELLYFIN_USER_ID names a jellyfin administrator, so every member without a link would watch with its access "
            "and write to its history. Set it to an ordinary account, or leave it unset so members link their own."
        )
    if policy.get("IsDisabled"):
        raise SharedAccountError(f"JELLYFIN_USER_ID ({JELLYFIN_USER_ID}) names a disabled jellyfin user.")
    _checked_user_id = JELLYFIN_USER_ID


async def startup_problem():
    """`check_shared_account` as a pre-flight message; jellyfin being unreachable leaves the check to first use."""
    try:
        await check_shared_account()
    except SharedAccountError as err:
        return str(err)
    except ServiceError as err:
        print(f"could not check JELLYFIN_USER_ID yet, checking at first use: {err}", file=sys.stderr)
    return None


async def resolve_user_id():
    """The shared account for a member without a link, or None when there is none or it was refused."""
    if not JELLYFIN_USER_ID:
        return None
    try:
        await check_shared_account()
    except SharedAccountError as err:
        print(f"not using the shared account: {err}", file=sys.stderr)
        return None
    return JELLYFIN_USER_ID


def saved_position_seconds(item, duration_seconds):
    """A position worth offering: past the first half-minute and not within the last minute, else 0."""
    ticks = (item.get("UserData") or {}).get("PlaybackPositionTicks") or 0
    seconds = ticks / TICKS_PER_SECOND
    if seconds < MIN_RESUME_SECONDS:
        return 0.0
    if duration_seconds and seconds > duration_seconds - NEAR_END_SECONDS:
        return 0.0
    return seconds


async def fetch_last_watched(user_id=None):
    """The most recently played unfinished video for the account, or None; `/UserItems/Resume` is newest first."""
    user_id = user_id or await resolve_user_id()
    if user_id is None:
        return None
    params = {"userId": user_id, "mediaTypes": "Video", "limit": 1, "fields": jellyfin_core.STREAM_FIELDS}
    items = (await jellyfin_core.jf_get("/UserItems/Resume", params)).get("Items", [])
    return items[0] if items else None


async def report_position(item_id, seconds, *, finished=False, user_id=None):
    """Writes the position through `POST /UserItems/{id}/UserData`; finishing clears it and marks the item played."""
    user_id = user_id or await resolve_user_id()
    if user_id is None:
        return
    body = {
        "PlaybackPositionTicks": 0 if finished else int(seconds * TICKS_PER_SECOND),
        "LastPlayedDate": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if finished:
        body["Played"] = True
    await jellyfin_core.service().post(f"/UserItems/{item_id}/UserData", body, {"userId": user_id})


def report_due(last_reported_at, now=None):
    return (now if now is not None else time.monotonic()) - last_reported_at >= REPORT_EVERY_SECONDS
