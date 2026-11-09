"""The rest-of-message argument keeps its line breaks and spacing, and any whitespace may follow a command name."""

import pytest

from slimbots import Bot, Duration
from slimbots.authors import AuthorFilter
from slimbots.commands import split_first_word
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
    seen = []

    @bot.command(name="say")
    async def say(ctx, text: str):
        seen.append(text)

    @bot.command(name="remind")
    async def remind(ctx, duration: Duration, text: str = "nothing"):
        seen.append((int(duration), text))

    @bot.group(name="note")
    async def note(ctx, text: str = ""):
        seen.append(("note", text))

    @note.command(name="add")
    async def note_add(ctx, text: str):
        seen.append(("add", text))

    return bot, seen


async def run(bot, content):
    await bot.process_message({"id": "m1", "author_id": "u1", "channel_id": "c1", "content": content})


@pytest.mark.parametrize("text", [
    "first line\nsecond line",
    "a  b   c",
    "```\ndef f():\n    return 1\n```",
    "tabs\there",
])
async def test_the_rest_argument_arrives_exactly_as_typed(rig, text):
    bot, seen = rig
    await run(bot, f"!say {text}")
    assert seen == [text]


async def test_trailing_whitespace_is_dropped_but_inner_breaks_stay(rig):
    bot, seen = rig
    await run(bot, "!say one\n\ntwo  \n")
    assert seen == ["one\n\ntwo"]


@pytest.mark.parametrize("separator", ["\n", "\t", "  ", " \n "])
async def test_any_whitespace_may_follow_the_command_name(rig, separator):
    bot, seen = rig
    await run(bot, f"!say{separator}hello there")
    assert seen == ["hello there"]


async def test_a_command_name_followed_by_a_line_break_still_needs_its_argument(rig):
    bot, seen = rig
    await run(bot, "!say\n")
    assert seen == []


async def test_typed_arguments_before_the_rest_still_split_on_any_whitespace(rig):
    bot, seen = rig
    await run(bot, "!remind  10m\ncall mum\nbefore  noon")
    assert seen == [(600, "call mum\nbefore  noon")]


async def test_a_group_dispatches_past_a_double_space_or_a_line_break(rig):
    bot, seen = rig
    await run(bot, "!note  add  buy milk\nand eggs")
    await run(bot, "!note\nadd\nshopping")
    assert seen == [("add", "buy milk\nand eggs"), ("add", "shopping")]


async def test_a_group_without_a_matching_subcommand_keeps_the_whole_text(rig):
    bot, seen = rig
    await run(bot, "!note first\nsecond")
    assert seen == [("note", "first\nsecond")]


async def test_on_command_not_found_gets_the_name_and_the_rest_split_on_any_whitespace(rig):
    bot, _ = rig
    found = []

    @bot.event
    async def on_command_not_found(ctx, err):
        found.append((ctx.invoked_with, ctx.raw_args))

    await run(bot, "!nope\nsome  text")
    assert found == [("nope", "some  text")]


def test_split_first_word():
    assert split_first_word("say hello") == ("say", "hello")
    assert split_first_word("say\n\n  hello  there\n") == ("say", "hello  there\n")
    assert split_first_word("say") == ("say", "")
    assert split_first_word("") == ("", "")
