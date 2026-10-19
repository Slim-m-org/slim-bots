import pytest

from slimbots import Embed, style


def test_each_kind_carries_its_palette_colour():
    assert style.info("a").to_wire()["color"] == style.INFO
    assert style.success("a").to_wire()["color"] == style.SUCCESS
    assert style.warn("a").to_wire()["color"] == style.WARN
    assert style.error("a").to_wire()["color"] == style.ERROR
    assert style.card("neutral", title="a").to_wire()["color"] == style.NEUTRAL


def test_an_explicit_colour_beats_the_kind():
    assert style.card("info", title="a", color=0x123456).to_wire()["color"] == 0x123456


def test_an_unknown_kind_is_refused():
    with pytest.raises(ValueError, match="kind"):
        style.card("loud")


def test_a_card_is_a_plain_embed_with_every_field_passed_through():
    embed = style.card("info", title="t", description="d", footer_text="f", url="https://x.test", timestamp=5)
    assert isinstance(embed, Embed)
    assert embed.to_wire() == {
        "title": "t", "description": "d", "url": "https://x.test", "color": style.INFO,
        "footer": {"text": "f"}, "timestamp": 5,
    }


def test_footer_joins_lowercase_facts_and_drops_blanks():
    assert style.footer("steam", None, " ", "balance: 5") == "steam · balance: 5"
    assert style.footer(None, "") is None


def test_error_text_is_one_sentence_with_an_optional_hint():
    assert style.error_text("can't reach the panel") == "can't reach the panel."
    assert style.error_text("can't reach the panel.", "try again in a minute.") == "can't reach the panel. try again in a minute."


@pytest.mark.parametrize(
    ("examples", "expected"),
    [(("!a",), "try `!a`."), (("!a", "!b"), "try `!a` or `!b`."), (("!a", "!b", "!c"), "try `!a`, `!b` or `!c`.")],
)
def test_usage_text_lists_the_examples_as_code(examples, expected):
    assert style.usage_text(*examples) == expected


def test_usage_text_needs_an_example():
    with pytest.raises(ValueError):
        style.usage_text()


def test_send_kwargs_carries_the_wire_and_the_fallback():
    embed = style.info("title", "body")
    kwargs = style.send_kwargs(embed)
    assert kwargs["embeds"] == [embed.to_wire()]
    assert kwargs["fallback_content"] == embed.render_fallback()
    assert style.send_kwargs(embed, "plain")["fallback_content"] == "plain"
