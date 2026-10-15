"""`Space.channel_label` and `components.grid`."""

from slimbots import Button
from slimbots.components import grid
from slimbots.models import Channel
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient


def make_space():
    space = Space(FakeAsyncClient())
    space.channels = {"c1": Channel({"id": "c1", "name": "voice-room", "kind": "voice"})}
    return space


def test_a_known_channel_is_labelled_with_a_hash_and_its_name():
    assert make_space().channel_label("c1") == "#voice-room"
    assert make_space().channel_label("c1", "elsewhere") == "#voice-room"


def test_an_unknown_channel_falls_back_to_the_given_text_else_its_id():
    assert make_space().channel_label("gone") == "gone"
    assert make_space().channel_label("gone", "another channel") == "another channel"
    assert make_space().channel_label(None, "none set") == "none set"
    assert make_space().channel_label(None) == ""


def test_grid_splits_buttons_in_order_into_rows():
    buttons = [Button(str(i), f"b{i}") for i in range(7)]
    assert [[b.label for b in row] for row in grid(buttons, 3)] == [["0", "1", "2"], ["3", "4", "5"], ["6"]]
    assert [len(row) for row in grid(buttons)] == [5, 2]
    assert grid([]) == []
