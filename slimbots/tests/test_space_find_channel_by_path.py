"""`Space.find_channel_by_path`: `category/channel` or a bare `channel`, matched by name without case."""

from slimbots.space import Space
from slimbots.testing import FakeAsyncClient

CHANNELS = [
    {"id": "c1", "name": "logs", "category_id": "cat-a"},
    {"id": "c2", "name": "Logs", "category_id": "cat-b"},
    {"id": "c3", "name": "welcome", "category_id": None},
]
CATEGORIES = [{"id": "cat-a", "name": "Staff"}, {"id": "cat-b", "name": "Public"}]


def make_space():
    client = FakeAsyncClient()
    client.respond("GET", "/channels", CHANNELS)
    client.respond("GET", "/categories", CATEGORIES)
    return Space(client)


async def test_a_bare_name_finds_the_first_channel_with_that_name():
    assert await make_space().find_channel_by_path("WELCOME") == "c3"


async def test_a_category_path_picks_the_channel_in_that_category():
    space = make_space()
    assert await space.find_channel_by_path("public/logs") == "c2"
    assert await space.find_channel_by_path("Staff/Logs") == "c1"


async def test_nothing_matching_reads_as_none():
    space = make_space()
    assert await space.find_channel_by_path("nope") is None
    assert await space.find_channel_by_path("Staff/welcome") is None
    assert await space.find_channel_by_path("Nowhere/logs") is None
