"""Reads and writes a Jellyfin user's own playback position, so Continue Watching stays right; see README.md.
The API key can act as any user; the shared default is `JELLYFIN_USER_ID`, else the first enabled user, and a linked member uses theirs."""

from __future__ import annotations

import time
from datetime import datetime, timezone

import jellyfin_core

TICKS_PER_SECOND = 10_000_000
MIN_RESUME_SECONDS = 30
NEAR_END_SECONDS = 60
REPORT_EVERY_SECONDS = 30

JELLYFIN_USER_ID = ""

_resolved_user_id = None


def configure(bot):
    global JELLYFIN_USER_ID
    JELLYFIN_USER_ID = bot.setting("JELLYFIN_USER_ID", "") or ""


async def resolve_user_id():
    """The configured user, else the first enabled account `GET /Users` lists; None when neither exists."""
    global _resolved_user_id
    if JELLYFIN_USER_ID:
        return JELLYFIN_USER_ID
    if _resolved_user_id is None:
        users = await jellyfin_core.jf_get("/Users")
        enabled = [u for u in users if not (u.get("Policy") or {}).get("IsDisabled")]
        _resolved_user_id = enabled[0]["Id"] if enabled else None
    return _resolved_user_id


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
