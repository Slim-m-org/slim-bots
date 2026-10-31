#!/usr/bin/env python3
"""The shared stream rests while everyone plays the title themselves (slim-m decision 0050); run directly: python3 test_direct_play.py."""

import asyncio

import accounts
import direct_play
import jellyfin_core
import session_registry
import stream_session
import watch_sync
from slimbots import ApiError
from slimbots.models import Member
from slimbots.testing import FakeVoiceSession, human, run_module_tests
from test_bot import _fake_start_pipeline, jellyfin, movie_for_watch, setup_with_voice

OFFER_URL = "http://10.0.0.5:8096"


def link(verified_user, unverified_user):
    async def put():
        await jellyfin.bot.store.run(accounts.set_link, verified_user, "jf-a", "alice", True)
        await jellyfin.bot.store.run(accounts.set_link, unverified_user, "jf-b", "bob", False)

    asyncio.run(put())


def scenario(body, *, direct_ids=(), people=("u1", "u2"), links=None):
    """Runs `body(client, session, voice, state)` on a started party whose roster and direct viewers the body can change."""
    client = setup_with_voice()
    if links:
        link(*links)
    jellyfin.bot.space.members["u2"] = Member(human("u2", "bea", "Bea", role_ids=[]))
    state = {"people": list(people), "direct": list(direct_ids), "session_down": False}

    def roster():
        return {"participants": [{"user_id": u} for u in ["bot-1", *state["people"]]]}

    def session_state():
        if state["session_down"]:
            raise ApiError(500, {})
        return {"direct_viewer_ids": state["direct"]}

    client.respond("GET", "/channels/c1/voice/roster", roster)
    client.respond("GET", "/channels/c1/watch-session", session_state)
    saved = (stream_session.WatchSession._start_pipeline, direct_play.LIFT_AFTER_SECONDS, jellyfin_core.JELLYFIN_DIRECT_URL)
    stream_session.WatchSession._start_pipeline = _fake_start_pipeline
    direct_play.LIFT_AFTER_SECONDS = 0.0
    jellyfin_core.JELLYFIN_DIRECT_URL = OFFER_URL

    async def run():
        voice = FakeVoiceSession("c1")
        session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", voice)
        await session.start(30.0)
        await session.direct_host.stop()
        try:
            await body(client, session, voice, state)
        finally:
            session.finished = True
            if session._monitor_task is not None:
                session._monitor_task.cancel()
            await session.sync.end()
            for task in list(jellyfin.bot._background_tasks):
                task.cancel()

    try:
        asyncio.run(run())
    finally:
        stream_session.WatchSession._start_pipeline, direct_play.LIFT_AFTER_SECONDS, jellyfin_core.JELLYFIN_DIRECT_URL = saved
        session_registry.clear()


def test_the_offer_names_verified_and_unverified_members_apart():
    assert direct_play.build_offer("", {"a": True}) is None
    offer = direct_play.build_offer(OFFER_URL, {"b": False, "a": True})
    assert offer == {"base_url": OFFER_URL, "verified_user_ids": ["a"], "unverified_user_ids": ["b"]}


def test_anyone_not_playing_directly_keeps_the_floor():
    assert direct_play.needs_floor(["a", "b"], {"a"}) is True
    assert direct_play.needs_floor(["a", "b"], {"a", "b", "c"}) is False


def test_the_offer_reaches_the_server_with_who_is_linked_how():
    watch_sync.MIN_GAP_SECONDS = 0.0

    async def body(client, session, voice, state):
        await session.direct_host._step()
        assert session.sync.direct_play == {"base_url": OFFER_URL, "verified_user_ids": ["u1"], "unverified_user_ids": ["u2"]}
        await asyncio.sleep(0.3)
        puts = [b for m, p, b, _ in client.calls if m == "PUT" and p.endswith("/watch-session") and b.get("direct_play")]
        assert puts and puts[-1]["direct_play"]["verified_user_ids"] == ["u1"]

    scenario(body, links=("u1", "u2"))


def test_a_mixed_room_keeps_the_encode_running():
    async def body(client, session, voice, state):
        state["direct"] = ["u1"]
        await session.direct_host._step()
        assert not session.floor_lifted and voice.unpublish_count == 0

    scenario(body)


def test_the_floor_lifts_when_everyone_is_direct_and_comes_back_at_the_rooms_position():
    async def body(client, session, voice, state):
        state["direct"] = ["u1", "u2"]
        await session.direct_host._step()
        assert session.floor_lifted and voice.published is None and voice.unpublish_count == 1
        await asyncio.sleep(0.2)
        assert session.position_seconds >= 30.2
        state["direct"] = ["u1"]
        await session.direct_host._step()
        assert not session.floor_lifted and voice.published is not None and voice.publish_count == 2
        assert 30.2 <= session._seek_base < 31.0

    scenario(body)


def test_a_joiner_who_is_not_direct_brings_the_floor_back_at_once():
    async def body(client, session, voice, state):
        state["direct"] = ["u1", "u2"]
        await session.direct_host._step()
        state["people"].append("u3")
        jellyfin.bot.space.members["u3"] = Member(human("u3", "cy", "Cy", role_ids=[]))
        await session.direct_host._step()
        assert not session.floor_lifted and voice.published is not None

    scenario(body)


def test_an_unreadable_session_state_counts_as_needing_the_floor():
    async def body(client, session, voice, state):
        state["direct"] = ["u1", "u2"]
        await session.direct_host._step()
        assert session.floor_lifted
        state["session_down"] = True
        await session.direct_host._step()
        assert not session.floor_lifted and voice.published is not None

    scenario(body)


def test_a_flapping_report_does_not_lift_the_floor_before_it_has_held():
    async def body(client, session, voice, state):
        direct_play.LIFT_AFTER_SECONDS = 60.0
        state["direct"] = ["u1", "u2"]
        await session.direct_host._step()
        assert not session.floor_lifted

    scenario(body)


def test_a_bot_on_the_call_is_not_a_viewer_who_needs_the_floor():
    async def body(client, session, voice, state):
        jellyfin.bot.space.members["bot-2"] = Member(human("bot-2", "other", "Other", is_bot=True))
        state["people"].append("bot-2")
        state["direct"] = ["u1", "u2"]
        await session.direct_host._step()
        assert session.floor_lifted

    scenario(body)


def test_a_seek_while_the_floor_is_lifted_moves_the_position_without_launching_an_encode():
    async def body(client, session, voice, state):
        stream_session.WatchSession._start_pipeline = saved_real_start
        launched = []

        async def launch(start_seconds):
            launched.append(start_seconds)

        session._launch_pipeline = launch
        state["direct"] = ["u1", "u2"]
        await session.direct_host._step()
        await session.seek(600.0)
        assert launched == [] and 600.0 <= session.position_seconds < 601.0
        assert session.floor_lifted and voice.publish_count == 1

    saved_real_start = stream_session.WatchSession._start_pipeline
    scenario(body)


def test_a_quality_change_while_lifted_does_not_republish():
    async def body(client, session, voice, state):
        state["direct"] = ["u1", "u2"]
        await session.direct_host._step()
        await session.set_quality(next(q for q in __import__("quality").PRESETS.values() if q is not session.quality))
        assert voice.published is None and session.floor_lifted

    scenario(body)


def test_a_floor_that_cannot_be_restored_ends_the_party_instead_of_leaving_it_silent():
    async def body(client, session, voice, state):
        state["direct"] = ["u1", "u2"]
        await session.direct_host._step()
        state["direct"] = []

        async def refuse(**_kwargs):
            raise RuntimeError("livekit is down")

        voice.publish_screen_share = refuse
        for _ in range(direct_play.GIVE_UP_AFTER):
            await session.direct_host._step()
        assert session.finished and voice.left

    scenario(body)


def test_without_a_direct_url_the_host_never_starts_and_nothing_is_offered():
    client = setup_with_voice()
    saved = jellyfin_core.JELLYFIN_DIRECT_URL
    jellyfin_core.JELLYFIN_DIRECT_URL = ""
    try:
        session = stream_session.WatchSession(jellyfin.bot, "c1", "c1", movie_for_watch(), "u1", FakeVoiceSession("c1"))
        session.direct_host.start()
        assert session.direct_host._task is None
    finally:
        jellyfin_core.JELLYFIN_DIRECT_URL = saved
        session_registry.clear()


if __name__ == "__main__":
    run_module_tests(globals())
