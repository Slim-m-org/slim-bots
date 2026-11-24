#!/usr/bin/env python3
"""`!watch` streams into a call only for a member who may speak there themselves; run directly: python3 test_watch_speak.py."""

import asyncio

import jellyfin_core
import stream_session
from test_bot import grant_in, jellyfin, message
from test_resume import Patched, voice_client
from test_watch_launch import FOUND, started
from slimbots.http import ApiError
from slimbots.permissions import Permissions
from slimbots.testing import run_module_tests


def watch(client):
    with Patched((stream_session.WatchSession, "start", started), *FOUND):
        asyncio.run(jellyfin.bot.process_message(message("!watch inception")))
    return [m.get("content") or "" for m in client.ephemerals + client.sent]


def test_a_listen_only_member_cannot_start_a_watch_party():
    client = voice_client()
    grant_in(client, "u1", "v1", Permissions.VIEW_CHANNEL | Permissions.CONNECT)
    said = watch(client)
    assert not jellyfin.bot.voice.sessions
    assert any("Speak" in line and "voice-room" in line for line in said), said


def test_a_listen_only_member_is_refused_before_any_search():
    client = voice_client()
    grant_in(client, "u1", "v1", Permissions.VIEW_CHANNEL | Permissions.CONNECT)
    searched = []

    async def search(query, limit, user_id=None):
        searched.append(query)
        return []

    with Patched((jellyfin_core, "watch_search", search)):
        asyncio.run(jellyfin.bot.process_message(message("!watch inception")))
    assert not searched


def test_unreadable_rights_refuse_the_watch_party():
    client = voice_client()
    client.respond("GET", "/members/u1/permissions?channel_id=v1", ApiError(500, {"error": "boom"}))
    watch(client)
    assert not jellyfin.bot.voice.sessions


def test_a_member_who_may_speak_starts_it():
    client = voice_client()
    watch(client)
    assert len(jellyfin.bot.voice.sessions) == 1


if __name__ == "__main__":
    run_module_tests(globals())
