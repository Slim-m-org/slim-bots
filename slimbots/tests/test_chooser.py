"""`Chooser`: one message of buttons only the invoker may press, closed by a choice, a cancel or the clock."""

import asyncio

import pytest

from slimbots.bot import Bot
from slimbots.chooser import Chooser, Pick
from slimbots.testing import FakeAsyncClient


class Client(FakeAsyncClient):
    def __init__(self):
        super().__init__()
        self.edited = []

    async def edit_message(self, channel_id, message_id, content, *, embeds=None):
        self.edited.append(content)


def make_bot():
    bot = Bot(prefix="!")
    bot.client = Client()
    return bot


def press(custom_id, message_id, user_id="u1"):
    return {
        "type": "interaction.created", "interaction_id": f"i-{custom_id}", "channel_id": "c1", "message_id": message_id,
        "custom_id": custom_id, "user_id": user_id, "user_display_name": user_id, "created_at": 1,
    }


async def settle(bot):
    while True:
        pending = [t for t in bot._background_tasks if "pick-" not in t.get_name()]
        if not pending:
            return
        await asyncio.gather(*pending)


ITEMS = [{"name": "Alpha"}, {"name": "Beta"}, {"name": "x" * 200}]


async def opened(chosen, **options):
    bot = make_bot()
    chooser = Chooser("demo:", "demo add", lambda item: item["name"], "add", **options)
    chooser.register(bot)

    async def on_choose(item):
        chosen.append(item["name"])
        return f"added {item['name']}"

    pick = Pick("u1", "Alice", "c1", ITEMS, "al", on_choose)
    await chooser.open(bot, pick, reply_to_id="m0")
    return bot, chooser, pick


async def test_the_chooser_posts_labelled_buttons_two_to_a_row_and_a_cancel():
    bot, chooser, pick = await opened([])
    sent = bot.client.sent[0]
    assert sent["content"] == '3 match(es) for "al" - Alice, pick one to add.' and sent["reply_to_id"] == "m0"
    rows = [[b["custom_id"] for b in row["buttons"]] for row in sent["components"]]
    assert rows == [["demo:sel:0", "demo:sel:1"], ["demo:sel:2"], ["demo:cancel"]]
    assert len(sent["components"][1]["buttons"][0]["label"]) == 80 and sent["components"][1]["buttons"][0]["label"].endswith("~")
    assert chooser._picks == {pick.message_id: pick}


async def test_choosing_runs_on_choose_once_and_closes_with_its_outcome():
    chosen = []
    bot, chooser, pick = await opened(chosen)
    for _ in range(2):
        await bot._handle_frame(press("demo:sel:1", pick.message_id))
        await settle(bot)
    assert chosen == ["Beta"] and not chooser._picks
    assert bot.client.edited == ["added Beta"]
    assert bot.client.component_edits[-1]["components"] == []


async def test_cancel_closes_without_choosing():
    chosen = []
    bot, chooser, pick = await opened(chosen)
    await bot._handle_frame(press("demo:cancel", pick.message_id))
    await settle(bot)
    assert chosen == [] and not chooser._picks
    assert bot.client.edited == ["cancelled."]


@pytest.mark.parametrize("custom_id", ["demo:sel:abc", "demo:sel:9", "demo:nuke", "demo:sel:-1"])
async def test_a_malformed_or_out_of_range_press_chooses_nothing_and_leaves_the_chooser_open(custom_id):
    chosen = []
    bot, chooser, pick = await opened(chosen)
    await bot._handle_frame(press(custom_id, pick.message_id))
    await settle(bot)
    assert chosen == [] and chooser._picks and bot.client.acks


async def test_someone_else_is_told_privately_and_nothing_happens():
    chosen = []
    bot, chooser, pick = await opened(chosen)
    await bot._handle_frame(press("demo:sel:0", pick.message_id, user_id="u2"))
    await settle(bot)
    assert chosen == [] and chooser._picks
    assert bot.client.ephemerals[-1]["content"] == "only Alice can choose here - `!demo add` starts your own."


async def test_a_press_on_an_expired_pick_closes_it_and_answers_privately():
    bot, chooser, pick = await opened([])
    pick.created_at -= 301
    await bot._handle_frame(press("demo:sel:0", pick.message_id))
    await settle(bot)
    assert not chooser._picks
    assert bot.client.edited == ["timed out - `!demo add` again to retry."]
    assert bot.client.ephemerals[-1]["content"] == "that choice has expired - run `!demo add` again."


async def test_the_expired_reply_wording_is_the_bots_to_set():
    bot, _, pick = await opened([], expired_reply="gone - {retry} again.")
    pick.created_at -= 301
    await bot._handle_frame(press("demo:sel:0", pick.message_id))
    await settle(bot)
    assert bot.client.ephemerals[-1]["content"] == "gone - `!demo add` again."


async def test_a_press_on_an_unknown_message_is_told_it_expired():
    bot, _, _ = await opened([])
    await bot._handle_frame(press("demo:sel:0", "other"))
    await settle(bot)
    assert "expired" in bot.client.ephemerals[-1]["content"]


async def test_the_clock_closes_a_pick_that_nobody_answered():
    bot, chooser, pick = await opened([])
    await chooser.expire(bot, pick, 0)
    assert not chooser._picks
    assert bot.client.edited == ["timed out - `!demo add` again to retry."]
    await chooser.expire(bot, pick, 0)


async def test_a_failing_choice_still_closes_the_chooser_with_a_retry_hint(capsys):
    bot = make_bot()
    chooser = Chooser("demo:", "demo add", lambda item: item["name"], "add")
    chooser.register(bot)

    async def broken(item):
        raise RuntimeError("boom")

    pick = Pick("u1", "Alice", "c1", ITEMS, "al", broken)
    await chooser.open(bot, pick)
    await bot._handle_frame(press("demo:sel:0", pick.message_id))
    await settle(bot)
    assert not chooser._picks
    assert bot.client.edited == ["could not add that - `!demo add` again to retry."]
    assert "could not act on a pick: boom" in capsys.readouterr().err
