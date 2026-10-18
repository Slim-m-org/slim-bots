import pytest

from slimbots import Bot, ValidationError, require_len
from slimbots.authors import AuthorFilter
from slimbots.exceptions import BadArgument
from slimbots.space import Space
from slimbots.testing import FakeAsyncClient


@pytest.fixture
async def rig():
    client = FakeAsyncClient(me_id="bot-1")
    bot = Bot(prefix="!")
    bot.client = client
    bot.space = Space(client)
    bot.authors = AuthorFilter(client, space=bot.space)
    bot.me_id = "bot-1"
    client.respond("GET", "/members", [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}])
    await bot.space.refresh_members()
    return bot, client


def test_a_validation_error_is_a_bad_argument_and_still_a_value_error():
    assert issubclass(ValidationError, BadArgument) and issubclass(ValidationError, ValueError)


async def test_a_command_that_lets_a_validation_error_escape_replies_with_its_text(rig):
    bot, client = rig

    @bot.command(name="note")
    async def note(ctx, text: str):
        require_len(text, max_len=3, field="a note")

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!note toolong"})
    assert client.sent[-1]["content"] == "a note must be between 0 and 3 characters"


async def test_a_validation_error_reaches_on_command_error(rig):
    bot, client = rig
    seen = []

    @bot.event
    async def on_command_error(ctx, err):
        seen.append(err)

    @bot.command(name="note")
    async def note(ctx, text: str):
        require_len(text, max_len=1)

    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": "!note xx"})
    assert len(seen) == 1 and isinstance(seen[0], ValidationError)
