#!/usr/bin/env python3
"""`!yt` against FakeAsyncClient and FakeVoice, with the video lookup faked; run directly: python3 test_bot.py."""

import asyncio

import bot as yt
import party as parties
import youtube_core
from slimbots import Permissions
from slimbots.models import Channel
from slimbots.testing import FakeAsyncClient, FakeVoice, human, run_module_tests, wire_bot
from slimbots.voice import VoiceError

RICK = "https://youtu.be/dQw4w9WgXcQ?t=30"
ZOO = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
MEMBERS = [human("u1", "nick", "Nick"), human("u2", "sam", "Sam"), human("u3", "mgr", "Mgr", role_ids=["role-mgr"])]
ROLES = [{"id": "role-mgr", "name": "Manager", "permissions": Permissions.MANAGE_CHANNELS, "is_everyone": False}]


class FakeLookup:
    def __init__(self):
        self.refuse = set()
        self.missing = set()

    async def video(self, video_id):
        if video_id in self.refuse:
            raise youtube_core.EmbedRefused(video_id)
        if video_id in self.missing:
            raise youtube_core.NoSuchVideo(video_id)
        titles = {"dQw4w9WgXcQ": ("Never Gonna Give You Up", 214), "jNQXAC9IVRw": ("Me at the zoo", 19)}
        title, seconds = titles.get(video_id, ("Something @everyone", None))
        return youtube_core.Video(video_id, title, "A Channel", seconds)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class Rig:
    def __init__(self, member_channels=None, join_error=None):
        parties.clear()
        yt._start_locks.clear()
        self.lookup = yt.lookup = FakeLookup()
        self.clock = Clock()
        self.client = FakeAsyncClient(me_id="bot-1")
        self.client.respond("GET", "/members", MEMBERS)
        self.client.respond("GET", "/roles", ROLES)
        self.client.respond("GET", "/channels/v1/voice/roster", {"participants": [{"user_id": "u1"}, {"user_id": "bot-1"}]})
        yt.bot.channels = {"c1"}
        wire_bot(yt.bot, self.client, me_id="bot-1")
        yt.bot.voice = FakeVoice(member_channels={"u1": "v1", "u2": "v1", "u3": "v1"} if member_channels is None else member_channels,
                                 join_error=join_error)
        yt.bot.space.channels["v1"] = Channel({"id": "v1", "name": "lounge", "kind": "voice"})

    async def boot(self):
        await yt.bot.space.refresh_roles()
        await yt.bot.space.refresh_members()

    async def say(self, text, user="u1", msg_id="m1"):
        await yt.bot.process_message({"id": msg_id, "author_id": user, "channel_id": "c1", "content": text})
        await asyncio.sleep(0)

    def replies(self):
        return [m.get("content") or "" for m in self.client.ephemerals]

    def session_writes(self):
        return [body for method, path, body, _ in self.client.calls if method == "PUT" and path == "/channels/v1/watch-session"]

    def party(self):
        return parties.party_in("v1")


def run(rig, scenario):
    async def body():
        await rig.boot()
        original = parties.Party.__init__

        def with_clock(self, *args, **kwargs):
            kwargs.setdefault("clock", rig.clock)
            original(self, *args, **kwargs)

        parties.Party.__init__ = with_clock
        try:
            await scenario()
        finally:
            parties.Party.__init__ = original
            for party in parties.live_parties().values():
                await party.stop(reason="test over")

    asyncio.run(body())


def test_a_link_starts_a_youtube_session_in_the_invokers_call_and_posts_the_link_there():
    rig = Rig()

    async def scenario():
        await rig.say(f"!yt {RICK}")
        await asyncio.sleep(0.05)
        (write,) = rig.session_writes()[:1]
        assert write["provider"] == "youtube" and write["item_id"] == "dQw4w9WgXcQ"
        assert write["position_ms"] == 30000 and write["duration_ms"] == 214000 and write["playing"] is True
        assert write["controller_user_id"] == "u1"
        assert any("watching **Never Gonna Give You Up**" in r and "lounge" in r for r in rig.replies())
        posted = [m for m in rig.client.sent if m.get("channel_id") == "v1" or "watch?v=dQw4w9WgXcQ" in (m.get("content") or "")]
        assert posted and "https://www.youtube.com/watch?v=dQw4w9WgXcQ" in posted[0]["content"]

    run(rig, scenario)


def test_someone_not_in_a_call_is_told_to_join_one():
    rig = Rig(member_channels={})

    async def scenario():
        await rig.say(f"!yt {RICK}")
        assert any("join a voice channel first" in r for r in rig.replies())
        assert not yt.bot.voice.sessions

    run(rig, scenario)


def test_not_a_link_gets_the_shape_of_one():
    rig = Rig()

    async def scenario():
        await rig.say("!yt never gonna give you up")
        assert any("give a YouTube link" in r for r in rig.replies())

    run(rig, scenario)


def test_a_video_that_refuses_embedding_or_does_not_exist_is_refused_before_joining():
    rig = Rig()
    rig.lookup.refuse.add("dQw4w9WgXcQ")
    rig.lookup.missing.add("jNQXAC9IVRw")

    async def scenario():
        await rig.say(f"!yt {RICK}")
        await rig.say(f"!yt {ZOO}", msg_id="m2")
        replies = rig.replies()
        assert any("does not allow it to play outside YouTube" in r for r in replies)
        assert any("private, removed, or does not exist" in r for r in replies)
        assert not yt.bot.voice.sessions

    run(rig, scenario)


def test_a_second_link_queues_and_skip_plays_it():
    rig = Rig()

    async def scenario():
        await rig.say(f"!yt {RICK}")
        await rig.say(f"!yt {ZOO}", user="u2", msg_id="m2")
        assert any("number 1 in line" in r for r in rig.replies())
        await rig.say("!yt queue", msg_id="m3")
        assert any("Me at the zoo" in r and "Sam" in r for r in rig.replies())
        await rig.say("!yt skip", msg_id="m4")
        assert rig.party().current.video_id == "jNQXAC9IVRw"
        await asyncio.sleep(2.2)
        assert rig.session_writes()[-1]["item_id"] == "jNQXAC9IVRw"
        assert len(yt.bot.voice.sessions) == 1

    run(rig, scenario)


def test_pause_seek_and_resume_move_the_room_clock():
    rig = Rig()

    async def scenario():
        await rig.say(f"!yt {RICK}")
        rig.clock.now += 10
        await rig.say("!yt pause", msg_id="m2")
        party = rig.party()
        assert party.paused and party.position_seconds == 40
        rig.clock.now += 100
        assert party.position_seconds == 40
        await rig.say("!yt seek 1:30", msg_id="m3")
        assert party.position_seconds == 90
        await rig.say("!yt resume", msg_id="m4")
        rig.clock.now += 5
        assert party.position_seconds == 95

    run(rig, scenario)


def test_only_the_starter_the_queuer_or_a_manager_may_steer():
    rig = Rig()

    async def scenario():
        await rig.say(f"!yt {RICK}")
        await rig.say("!yt pause", user="u2", msg_id="m2")
        assert not rig.party().paused
        assert any("only the person who started this party" in r for r in rig.replies())
        await rig.say("!yt pause", user="u3", msg_id="m3")
        assert rig.party().paused

    run(rig, scenario)


def test_a_video_that_ends_plays_the_next_and_the_last_one_ends_the_party():
    rig = Rig()

    async def scenario():
        await rig.say(f"!yt {ZOO}")
        await rig.say(f"!yt {RICK}", msg_id="m2")
        party = rig.party()
        rig.clock.now += 20
        await party._advance_if_ended()
        assert party.current.video_id == "dQw4w9WgXcQ"
        rig.clock.now += 215
        await party._advance_if_ended()
        assert party.finished and yt.bot.voice.sessions[0].left
        assert ("DELETE", "/channels/v1/watch-session") in [(m, p) for m, p, _, _ in rig.client.calls]

    run(rig, scenario)


def test_everyone_leaving_ends_the_party():
    rig = Rig()

    async def scenario():
        await rig.say(f"!yt {RICK}")
        rig.client.respond("GET", "/channels/v1/voice/roster", {"participants": [{"user_id": "bot-1"}]})
        party = rig.party()
        party.wake_monitor()
        for _ in range(20):
            await asyncio.sleep(0.01)
        assert party.finished

    run(rig, scenario)


def test_a_join_failure_says_why():
    rig = Rig(join_error=VoiceError("needs VIEW_CHANNEL and CONNECT in that channel"))

    async def scenario():
        await rig.say(f"!yt {RICK}")
        assert any("can't join #lounge" in r for r in rig.replies())
        assert rig.party() is None

    run(rig, scenario)


def test_two_starts_at_once_make_one_party_and_a_queue():
    rig = Rig()

    async def scenario():
        await asyncio.gather(rig.say(f"!yt {RICK}"), rig.say(f"!yt {ZOO}", user="u2", msg_id="m2"))
        assert len(yt.bot.voice.sessions) == 1
        assert len(rig.party().queue) == 1

    run(rig, scenario)


def test_titles_cannot_mention_or_format():
    rig = Rig()

    async def scenario():
        await rig.say("!yt https://youtu.be/aaaaaaaaaaa")
        assert any("Something \\@everyone" in r for r in rig.replies())

    run(rig, scenario)


if __name__ == "__main__":
    run_module_tests(globals())
