import datetime

import pytest

from slimbots import Button, Embed, rows
from slimbots.context import Context
from slimbots.http import ApiError
from slimbots.testing import FakeAsyncClient


def test_to_wire_only_includes_fields_that_were_set():
    embed = Embed(title="Hand", description="you win").add_field("stake", "10", inline=True)
    wire = embed.to_wire()
    assert wire == {"title": "Hand", "description": "you win", "fields": [{"name": "stake", "value": "10", "inline": True}]}


def test_to_wire_nests_footer_author_image_thumbnail():
    embed = Embed(footer="a footer").set_author("bot", url="https://x").set_image("https://img").set_thumbnail("https://thumb")
    wire = embed.to_wire()
    assert wire["footer"] == {"text": "a footer"}
    assert wire["author"] == {"name": "bot", "url": "https://x"}
    assert wire["image"] == {"url": "https://img"}
    assert wire["thumbnail"] == {"url": "https://thumb"}


def test_fields_are_capped_at_25():
    embed = Embed()
    for i in range(30):
        embed.add_field(str(i), str(i))
    assert len(embed.fields) == 25


def test_render_fallback_is_never_blank():
    assert Embed().render_fallback() == "(embed)"


async def test_send_with_embed_posts_the_real_wire_shape():
    client = FakeAsyncClient()
    embed = Embed(title="Balance").add_field("chips", "100")
    await client.send("c1", "", embeds=[embed.to_wire()], fallback_content=embed.render_fallback())
    sent = client.sent[-1]
    assert sent["embeds"] == [embed.to_wire()]


async def test_send_falls_back_to_plain_text_when_the_server_rejects_embeds():
    client = FakeAsyncClient()
    attempts = {"n": 0}

    def reject_the_first_attempt_only():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ApiError(400, {"error": "unknown field embeds"})
        return {"id": "m1", "seq": 1}

    client.respond("POST", "/channels/c1/messages", reject_the_first_attempt_only)
    embed = Embed(title="Balance").add_field("chips", "100")
    await client.send("c1", "Balance", embeds=[embed.to_wire()], fallback_content="Balance\nfallback text")

    assert attempts["n"] == 2
    assert client.calls[-1][2]["content"] == "Balance\nfallback text"
    assert "embeds" not in client.calls[-1][2]


WHEN = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
WHEN_MS = 1767225600000


@pytest.mark.parametrize("given", [WHEN, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00+00:00", WHEN_MS, datetime.datetime(2026, 1, 1)])
def test_timestamp_goes_on_the_wire_as_unix_millis(given):
    assert Embed(title="t", timestamp=given).to_wire()["timestamp"] == WHEN_MS


@pytest.mark.parametrize("given", ["yesterday", "", 1.5, True])
def test_a_timestamp_nobody_can_read_is_refused_when_the_embed_is_built(given):
    with pytest.raises(ValueError, match="timestamp"):
        Embed(title="t", timestamp=given)


async def test_an_embed_with_an_iso_timestamp_is_not_downgraded_to_text():
    client = FakeAsyncClient()

    def reject_a_non_integer_timestamp(body):
        return any(not isinstance(e.get("timestamp", 0), int) for e in body.get("embeds", []))

    real_call = client.call

    async def call(method, path, body=None, **kw):
        if method == "POST" and reject_a_non_integer_timestamp(body or {}):
            raise ApiError(400, {"error": "invalid type: string, expected i64"})
        return await real_call(method, path, body, **kw)

    client.call = call
    client.respond("POST", "/channels/c1/messages", {"id": "m1", "seq": 1})
    embed = Embed(title="t", timestamp="2026-01-01T00:00:00Z")
    await client.send("c1", "x", embeds=[embed.to_wire()], fallback_content=embed.render_fallback())
    posts = [c[2] for c in client.calls if c[0] == "POST" and c[1].endswith("/messages")]
    assert len(posts) == 1 and "embeds" in posts[0]


def _rejecting_once(status):
    client = FakeAsyncClient()
    attempts = {"n": 0}

    def respond():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ApiError(status, {"error": "refused"})
        return {"id": "m1", "seq": 1}

    client.respond("POST", "/channels/c1/messages", respond)
    return client, attempts


async def test_the_plain_text_fallback_keeps_buttons_replies_and_attachments():
    client, attempts = _rejecting_once(400)
    await client.send(
        "c1", "", embeds=[{"title": "t"}], fallback_content="x", components=rows([Button("Pause", "pause")]),
        reply_to_id="m0", attachment_ids=["a1"], message_id="m1",
    )
    sent = client.calls[-1][2]
    assert attempts["n"] == 2
    assert sent == {
        "id": "m1", "content": "x", "reply_to_id": "m0", "attachment_ids": ["a1"],
        "components": [{"buttons": [{"label": "Pause", "style": "secondary", "custom_id": "pause"}]}],
    }


@pytest.mark.parametrize("status", [401, 403, 429, 500, None])
async def test_only_a_400_falls_back_to_plain_text(status):
    client, attempts = _rejecting_once(status)
    with pytest.raises(ApiError):
        await client.send("c1", "", embeds=[{"title": "t"}], fallback_content="x")
    assert attempts["n"] == 1


def _ctx():
    class FakeBot:
        pass

    bot = FakeBot()
    bot.client = FakeAsyncClient()
    return Context(bot=bot, message={"id": "m1"}, author=None, channel_id="c1"), bot.client


async def test_context_reply_with_only_an_embed_never_sends_blank_content():
    ctx, client = _ctx()
    await ctx.reply(embed=Embed(title="Balance").add_field("chips", "100"))
    content = client.calls[-1][2]["content"]
    assert isinstance(content, str)
    assert content != ""
    assert "Balance" in content
    assert client.calls[-1][2]["embeds"]


async def test_context_reply_with_text_and_embed_keeps_the_embed_out_of_content():
    ctx, client = _ctx()
    await ctx.reply("here you go", embed=Embed(title="Balance"))
    assert client.calls[-1][2]["content"] == "here you go"
