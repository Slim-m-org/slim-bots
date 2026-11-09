"""A voice session's life after join: a refused heartbeat, a dropped room, a clean leave, and shutdown."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from slimbots import Bot, voice as voice_module
from slimbots.http import ApiError
from slimbots.testing import FakeAsyncClient
from test_voice import FakeRoom, fake_rtc_module


def make_bot(monkeypatch: pytest.MonkeyPatch, *channels: str) -> tuple[Bot, FakeAsyncClient, list[FakeRoom]]:
    """A bot with a fake client that can join each of `channels` (default c1), and the rooms those joins hand out."""
    bot, client = Bot(prefix="!"), FakeAsyncClient()
    rooms = [FakeRoom() for _ in channels or ("c1",)]
    for channel_id in channels or ("c1",):
        client.respond("POST", f"/channels/{channel_id}/voice/token", {
            "url": "wss://fake.invalid", "room": f"channel-{channel_id}", "token": "tok", "expires_at": 0, "can_publish": True,
        })
        client.respond("POST", f"/channels/{channel_id}/voice/heartbeat", None)
        client.respond("DELETE", f"/channels/{channel_id}/voice/heartbeat", None)
    bot.client = client
    queue = list(rooms)
    rtc = fake_rtc_module(rooms[0])
    rtc.Room = lambda: queue.pop(0)
    monkeypatch.setattr(voice_module, "load_rtc", lambda: rtc)
    return bot, client, rooms


def forgot_heartbeat(client: FakeAsyncClient, channel_id: str) -> bool:
    return ("DELETE", f"/channels/{channel_id}/voice/heartbeat", None, None) in client.calls


def callback_that_sets(event: asyncio.Event, ran: list[str] | None = None) -> Any:
    async def callback() -> None:
        if ran is not None:
            ran.append("ran")
        event.set()

    return callback


async def wait_for(event: asyncio.Event) -> None:
    await asyncio.wait_for(event.wait(), timeout=2)


@pytest.mark.parametrize("status", [403, 404, 501])
async def test_a_heartbeat_the_server_refuses_ends_only_that_session(monkeypatch: pytest.MonkeyPatch, status: int) -> None:
    bot, client, (room,) = make_bot(monkeypatch)
    client.respond("POST", "/channels/c1/voice/heartbeat", ApiError(status, {"error": "no"}))
    session = await bot.voice.join("c1")
    ended = asyncio.Event()
    session.on_disconnect(callback_that_sets(ended))
    await wait_for(ended)
    assert session.lost and room.disconnected and forgot_heartbeat(client, "c1")
    assert bot._fatal_error is None


async def test_a_heartbeat_that_fails_for_a_while_keeps_the_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(voice_module, "HEARTBEAT_INTERVAL_SECONDS", 0.001)
    bot, client, (room,) = make_bot(monkeypatch)
    beats: list[int] = []
    recovered = asyncio.Event()

    def flaky() -> None:
        beats.append(1)
        if len(beats) <= 2:
            raise ApiError(None, "connection refused")
        if len(beats) == 4:
            recovered.set()

    client.respond("POST", "/channels/c1/voice/heartbeat", flaky)
    session = await bot.voice.join("c1")
    await wait_for(recovered)
    assert not session.lost and not room.disconnected and bot._fatal_error is None
    await session.leave()


async def test_a_heartbeat_that_hits_a_revoked_token_still_stops_the_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, client, _rooms = make_bot(monkeypatch)
    client.respond("POST", "/channels/c1/voice/heartbeat", ApiError(401, {"error": "revoked"}))
    session = await bot.voice.join("c1")
    assert session._heartbeat_task is not None
    await asyncio.wait({session._heartbeat_task}, timeout=2)
    assert isinstance(bot._fatal_error, ApiError) and bot._fatal_error.status == 401


async def test_the_room_dropping_ends_the_session_and_tells_whoever_plays_into_it(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, client, (room,) = make_bot(monkeypatch)
    session = await bot.voice.join("c1")
    ended, ran = asyncio.Event(), []
    session.on_disconnect(callback_that_sets(ended, ran))
    room.emit("disconnected", 4)
    await wait_for(ended)
    assert session.lost and ran == ["ran"]
    assert session._heartbeat_task is None and forgot_heartbeat(client, "c1")
    assert bot.voice._sessions == set()


async def test_leaving_on_purpose_is_not_a_loss(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, client, (room,) = make_bot(monkeypatch)
    session = await bot.voice.join("c1")
    ran: list[str] = []
    session.on_disconnect(callback_that_sets(asyncio.Event(), ran))
    await session.leave()
    await asyncio.sleep(0)
    assert room.disconnected and not session.lost and ran == []
    assert [c for c in client.calls if c[0] == "DELETE"] == [("DELETE", "/channels/c1/voice/heartbeat", None, None)]


async def test_leaving_twice_forgets_the_heartbeat_once(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, client, _rooms = make_bot(monkeypatch)
    session = await bot.voice.join("c1")
    await asyncio.gather(session.leave(), session.leave())
    await session.leave()
    assert len([c for c in client.calls if c[0] == "DELETE"]) == 1


async def test_a_handler_added_after_the_loss_runs_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, _client, (room,) = make_bot(monkeypatch)
    session = await bot.voice.join("c1")
    room.emit("disconnected", 4)
    ran = asyncio.Event()
    session.on_disconnect(callback_that_sets(ran))
    await wait_for(ran)


async def test_a_failing_disconnect_handler_is_logged_and_does_not_stop_the_bot(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    bot, _client, (room,) = make_bot(monkeypatch)
    session = await bot.voice.join("c1")
    second = asyncio.Event()

    async def broken() -> None:
        raise RuntimeError("player exploded")

    session.on_disconnect(broken)
    session.on_disconnect(callback_that_sets(second))
    room.emit("disconnected", 4)
    await wait_for(second)
    assert bot._fatal_error is None and "voice disconnect handler for c1 failed" in caplog.text


async def test_leave_forgets_the_heartbeat_even_when_the_room_will_not_disconnect(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    bot, client, (room,) = make_bot(monkeypatch)
    room.disconnect_error = RuntimeError("already closed")
    session = await bot.voice.join("c1")
    with caplog.at_level("WARNING", logger="slimbots.voice"):
        await session.leave()
    assert forgot_heartbeat(client, "c1") and "could not disconnect from the voice room for c1" in caplog.text


async def test_a_failing_leave_does_not_hide_why_the_start_failed() -> None:
    class BrokenLeave:
        channel_id = "c1"

        async def leave(self) -> None:
            raise RuntimeError("leave blew up")

    async def start() -> None:
        raise RuntimeError("engine timed out")

    with pytest.raises(voice_module.VoiceError, match="could not start playing: engine timed out"):
        await voice_module.start_or_leave(BrokenLeave(), start(), doing="playing")


async def test_leave_all_leaves_every_open_session_once(monkeypatch: pytest.MonkeyPatch) -> None:
    bot, client, (first, second) = make_bot(monkeypatch, "c1", "c2")
    await bot.voice.join("c1")
    done = await bot.voice.join("c2")
    await done.leave()
    await bot.voice.leave_all()
    assert first.disconnected and second.disconnected
    assert forgot_heartbeat(client, "c1") and len([c for c in client.calls if c[0] == "DELETE"]) == 2
    assert bot.voice._sessions == set()


async def test_leave_all_gives_up_on_a_session_that_will_not_leave(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    bot, _client, (room,) = make_bot(monkeypatch)

    async def hang() -> None:
        await asyncio.sleep(30)

    room.disconnect = hang  # type: ignore[method-assign]
    monkeypatch.setattr(voice_module, "LEAVE_ALL_TIMEOUT_SECONDS", 0.01)
    await bot.voice.join("c1")
    with caplog.at_level("WARNING", logger="slimbots.voice"):
        await asyncio.wait_for(bot.voice.leave_all(), timeout=2)
    assert "gave up leaving 1 voice session" in caplog.text


async def test_shutdown_leaves_the_call_before_the_client_closes(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    monkeypatch.setenv("SLIMM_URL", "https://fake.invalid")
    monkeypatch.setenv("SLIMM_BOT_TOKEN", "slimbot_fake")
    bot, client, (room,) = make_bot(monkeypatch)
    bot.cursor_path = str(tmp_path / "cursor.db")
    joined = asyncio.Event()
    closed_with: list[bool] = []

    async def aclose() -> None:
        closed_with.append(forgot_heartbeat(client, "c1"))

    client.aclose = aclose  # type: ignore[method-assign]

    async def run_forever() -> int:
        bot.client = client
        await bot.voice.join("c1")
        joined.set()
        await asyncio.sleep(30)
        return 0

    monkeypatch.setattr(bot, "_run_forever", run_forever)
    monkeypatch.setattr("slimbots.bot.AsyncClient", lambda *_args: client)
    started = asyncio.create_task(bot.start())
    await wait_for(joined)
    started.cancel()
    with pytest.raises(asyncio.CancelledError):
        await started
    assert room.disconnected and closed_with == [True]
