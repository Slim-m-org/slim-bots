import pytest

from slimbots import Bot
from slimbots import events as ev
from slimbots.authors import AuthorFilter
from slimbots.bot import _CHANNEL_EVENT_FRAMES, _GLOBAL_EVENT_FRAMES
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

CHANNEL = {"id": "c9", "name": "new", "kind": "text"}

# One frame per typed kind, every field a distinct value so a swapped or hardcoded read shows up.
SAMPLES = {
    "presence.changed": {"user_id": "u7", "status": "away"},
    "profile.changed": {"user_id": "u7"},
    "channel.created": {"channel": CHANNEL},
    "channel.updated": {"channel": CHANNEL},
    "channel.deleted": {"channel_id": "c9"},
    "category.changed": {},
    "call.ringing": {"channel_id": "c1", "ring_id": "r1", "caller_id": "u2"},
    "call.ring_ended": {"channel_id": "c1", "ring_id": "r1", "outcome": "declined"},
    "reports.changed": {},
    "message.edited": {"channel_id": "c1", "seq": 5, "op_seq": 9, "message": {"id": "m1", "content": "new"}},
    "message.deleted": {"channel_id": "c1", "message_id": "m1", "op_seq": 11},
    "reactions.changed": {"channel_id": "c1", "message_id": "m1", "reactions": [{"emoji": "x", "count": 2}]},
    "thread.updated": {
        "channel_id": "c1", "parent_message_id": "m1", "thread_channel_id": "t1", "reply_count": 3, "last_reply_at": 1700,
    },
    "message.pinned": {"channel_id": "c1", "message_id": "m1", "pinned_by": "u3", "pinned_at": 1800},
    "message.unpinned": {"channel_id": "c1", "message_id": "m1"},
    "poll.voted": {"channel_id": "c1", "message_id": "m1", "options": [{"position": 0, "votes": 4}]},
    "typing.started": {"channel_id": "c1", "user_id": "u4"},
    "typing.stopped": {"channel_id": "c1", "user_id": "u4"},
    "overwrite.changed": {"channel_id": "c1"},
    "voice.activity": {"channel_id": "c1"},
    "canvas.objects.restored": {"channel_id": "c1", "seq": 6, "op_id": "op1", "object_ids": ["o1", "o2"]},
    "canvas.cursor.moved": {"channel_id": "c1", "user_id": "u5", "x": 1.5, "y": 2.5},
    "canvas.stroke_preview.updated": {
        "channel_id": "c1", "user_id": "u5", "object_id": "o3", "points": [[0, 0], [1, 1]], "ended": True,
    },
    "canvas.object.moved": {"channel_id": "c1", "seq": 7, "op_id": "op2", "object_id": "o4", "x": 1, "y": 2, "w": 3, "h": 4},
    "canvas.object.reordered": {"channel_id": "c1", "seq": 8, "op_id": "op3", "object_id": "o5", "z_index": 12},
    "canvas.media_slot.changed": {
        "channel_id": "c1", "kind": "screen", "user_id": "u6", "x": 1, "y": 2, "w": 3, "h": 4, "locked": True,
        "sent_to_back": False,
    },
}

ROUTES = {**_GLOBAL_EVENT_FRAMES, **_CHANNEL_EVENT_FRAMES}
TYPED = {kind: route for kind, route in ROUTES.items() if route[1] is not None}


@pytest.fixture
async def bot():
    client = FakeAsyncClient(me_id="bot-1")
    b = Bot(prefix="!", channels={"c1"})
    b.client, b.space, b.me_id = client, Space(client), "bot-1"
    b.authors = AuthorFilter(client, space=b.space)
    return b


def test_every_typed_frame_kind_has_a_sample():
    assert set(SAMPLES) == set(TYPED)


@pytest.mark.parametrize("kind", sorted(SAMPLES))
async def test_a_frame_reaches_its_handler_as_the_mapped_class_with_every_field(bot, kind):
    handler_name, payload_cls = TYPED[kind]
    seen = []

    async def handler(event):
        seen.append(event)

    handler.__name__ = handler_name
    bot.event(handler)
    await bot._handle_frame({"type": kind, **SAMPLES[kind]})

    assert len(seen) == 1
    assert type(seen[0]) is payload_cls
    for field, value in SAMPLES[kind].items():
        assert getattr(seen[0], field) == value, f"{payload_cls.__name__}.{field}"


def test_each_kind_maps_to_the_class_named_for_it():
    expected = {
        "call.ring_ended": ev.CallRingEnded, "canvas.object.reordered": ev.CanvasObjectReordered,
        "canvas.object.moved": ev.CanvasObjectMoved, "canvas.objects.restored": ev.CanvasObjectsRestored,
        "canvas.stroke_preview.updated": ev.CanvasStrokePreviewUpdated, "message.pinned": ev.MessagePinned,
        "message.unpinned": ev.MessageUnpinned, "thread.updated": ev.ThreadUpdated,
        "canvas.media_slot.changed": ev.CanvasMediaSlotChanged, "voice.activity": ev.VoiceActivityChanged,
    }
    for kind, cls in expected.items():
        assert TYPED[kind][1] is cls
