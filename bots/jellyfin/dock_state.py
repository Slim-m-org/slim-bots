"""Keeps the call dock's play/pause control showing what pressing it will do; see README.md."""

from __future__ import annotations

import contextlib

from slimbots import ApiError
from slimbots.registration import RegistrationRejected

import session_registry

PLAYPAUSE_ID = "jf-playpause"


def wanted(sessions):
    """The icon and label for the control: Play only when every live party is paused, else Pause."""
    parties = list(sessions)
    if parties and all(session.paused for session in parties):
        return "play", "Play"
    return "pause", "Pause"


async def sync(bot):
    """Best-effort: a failed dock update must never interrupt playback or a control's answer."""
    icon, label = wanted(session_registry.live_sessions().values())
    with contextlib.suppress(ApiError, RegistrationRejected):
        await bot.update_call_control(PLAYPAUSE_ID, icon=icon, label=label)
