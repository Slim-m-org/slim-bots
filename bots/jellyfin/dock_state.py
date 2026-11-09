"""Keeps the call dock's play/pause control showing what pressing it will do; see README.md."""

from __future__ import annotations

import contextlib

from slimbots import ApiError
from slimbots.registration import RegistrationRejected

import session_registry

PLAYPAUSE_ID = "jf-playpause"


def wanted(sessions):
    """The icon and label for the control: Play when every live party is paused, Pause when none is,
    and a label that promises neither when calls disagree, since one registration serves every call."""
    paused = [session.paused for session in sessions]
    if paused and all(paused):
        return "play", "Play"
    return ("pause", "Play/Pause") if any(paused) else ("pause", "Pause")


async def sync(bot):
    """Best-effort: a failed dock update must never interrupt playback or a control's answer."""
    icon, label = wanted(session_registry.live_sessions().values())
    with contextlib.suppress(ApiError, RegistrationRejected):
        await bot.update_call_control(PLAYPAUSE_ID, icon=icon, label=label)
