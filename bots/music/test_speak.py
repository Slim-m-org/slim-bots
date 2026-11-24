#!/usr/bin/env python3
"""`~play` speaks into a call only for a member who may speak there themselves; run directly: python3 test_speak.py."""

from slimbots.http import ApiError
from slimbots.permissions import Permissions
from slimbots.testing import run_module_tests

import music_cog
import bot as music
from test_bot import PUBLIC_URL, grant_in, replies, scenario, setup, until

LISTENER = Permissions.VIEW_CHANNEL | Permissions.CONNECT


def test_a_listen_only_member_cannot_play_into_their_call():
    client = setup()
    grant_in(client, "u1", "v1", LISTENER)
    scenario(client, f"~play {PUBLIC_URL}")
    assert not music.bot.voice.sessions
    assert "Speak" in replies(client)[-1] and "voice-room" in replies(client)[-1]


def test_a_listen_only_member_cannot_queue_into_a_call_already_playing():
    client = setup(member_channels={"u1": "v1", "u2": "v1"})
    grant_in(client, "u2", "v1", LISTENER)

    async def playing_long():
        await until(lambda: music_cog.active_sessions()["v1"].current is not None)

    async def queue_is_empty():
        assert len(music_cog.active_sessions()["v1"].queue) == 0

    scenario(client, "~play https://93.184.216.34/long.mp3", playing_long, ("u2", f"~play {PUBLIC_URL}"), queue_is_empty)
    assert not any("queued" in r for r in replies(client))
    assert any("Speak" in r for r in replies(client))


def test_unreadable_rights_refuse_to_play():
    client = setup()
    client.respond("GET", "/members/u1/permissions?channel_id=v1", ApiError(500, {"error": "boom"}))
    scenario(client, f"~play {PUBLIC_URL}")
    assert not music.bot.voice.sessions


if __name__ == "__main__":
    run_module_tests(globals())
