import pytest

from slimbots import Bot
from slimbots.registration import registration_body
from slimbots.testing import FakeAsyncClient


@pytest.fixture
def client():
    return FakeAsyncClient(me_id="bot-1")


@pytest.fixture
async def bot(client):
    from slimbots.authors import AuthorFilter
    from slimbots.space import Space

    b = Bot(prefix="!")
    b.client = client
    b.space = Space(client)
    b.authors = AuthorFilter(client, space=b.space)
    b.me_id = "bot-1"
    client.respond("GET", "/members", [{"id": "u1", "username": "nick", "display_name": "Nick", "is_bot": False, "is_webhook": False, "role_ids": []}])
    await b.space.refresh_members()
    return b


def _wire(bot):
    @bot.command(name="store", choices={"source": ("steam", "gog")})
    async def store(ctx, source: str, note: str = ""):
        await ctx.reply(f"picked {source}")

    return store


def _entry(bot, name):
    body = registration_body("!", bot.unique_commands())
    return next(c for c in body["commands"] if c["name"] == name)


async def send(bot, client, content):
    """The command's reply, public or private: errors answer only the invoker."""
    public, private = len(client.sent), len(client.ephemerals)
    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": content})
    if len(client.ephemerals) > private:
        return client.ephemerals[-1]["content"]
    assert len(client.sent) > public, "the command sent no reply at all"
    return client.sent[-1]["content"]


async def test_registration_advertises_every_argument_with_its_choices(bot):
    _wire(bot)
    entry = _entry(bot, "store")
    assert entry["options"] == [
        {"name": "source", "required": True, "choices": ["steam", "gog"]},
        {"name": "note", "required": False, "choices": []},
    ]


async def test_a_command_without_choices_sends_no_options(bot):
    @bot.command(name="ping")
    async def ping(ctx):
        await ctx.reply("pong")

    assert "options" not in _entry(bot, "ping")


async def test_a_listed_value_runs_in_its_declared_spelling(bot, client):
    _wire(bot)
    assert await send(bot, client, "!store STEAM") == "picked steam"


async def test_an_unlisted_value_is_refused_with_the_choices(bot, client):
    _wire(bot)
    reply = await send(bot, client, "!store origin")
    assert "`source` must be one of: steam, gog" in reply


def test_a_choice_for_a_parameter_that_does_not_exist_fails_at_declaration():
    bot = Bot(prefix="!")
    with pytest.raises(ValueError, match="not a parameter"):

        @bot.command(name="x", choices={"nope": ("a",)})
        async def x(ctx, source: str):
            pass


@pytest.mark.parametrize("values", [(), ("a", "A"), ("two words",), tuple(f"c{i}" for i in range(26))])
def test_a_choice_list_the_server_would_refuse_fails_at_declaration(values):
    bot = Bot(prefix="!")
    with pytest.raises(ValueError):

        @bot.command(name="x", choices={"source": values})
        async def x(ctx, source: str):
            pass


async def test_suggestions_are_advertised_as_choices_but_free_text_still_runs(bot, client):
    @bot.group(name="draw", suggest={"query": ("photo", "shaded", "undo")})
    async def draw(ctx, query: str = ""):
        await ctx.reply(f"free {query}")

    @draw.command(name="photo")
    async def photo(ctx, subject: str = ""):
        await ctx.reply(f"photo {subject}")

    assert _entry(bot, "draw")["options"] == [{"name": "query", "required": False, "choices": ["photo", "shaded", "undo"]}]
    assert await send(bot, client, "!draw michael jordan dunking") == "free michael jordan dunking"
    assert await send(bot, client, "!draw photo obama") == "photo obama"


async def test_a_suggestion_does_not_refuse_an_unlisted_word_on_a_plain_command(bot, client):
    @bot.command(name="mode", suggest={"kind": ("a", "b")})
    async def mode(ctx, kind: str):
        await ctx.reply(f"kind {kind}")

    assert _entry(bot, "mode")["options"] == [{"name": "kind", "required": True, "choices": ["a", "b"]}]
    assert await send(bot, client, "!mode zzz") == "kind zzz"


async def test_suggestions_are_checked_like_choices_when_declared(bot):
    with pytest.raises(ValueError):
        @bot.command(name="x", suggest={"nope": ("a",)})
        async def x(ctx, kind: str = ""):
            pass

    with pytest.raises(ValueError):
        @bot.command(name="y", suggest={"kind": ("a", "A")})
        async def y(ctx, kind: str = ""):
            pass
