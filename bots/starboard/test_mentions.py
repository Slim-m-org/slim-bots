#!/usr/bin/env python3
"""A highlight posted as the bot never carries a live mention from the quote or the author's name; run directly: python3 test_mentions.py."""

import os
import re
import sys

os.environ["STARBOARD_CHANNEL"] = "hl"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as starboard  # noqa: E402
from test_bot import member, setup  # noqa: E402

AT_SIGN = re.compile(r"(\\*)@")


def live_mentions(text):
    """The server's grammar: an `@` is plain text only when an odd run of backslashes precedes it."""
    return [m.start() for m in AT_SIGN.finditer(text) if len(m.group(1)) % 2 == 0]


def test_the_quote_is_defanged():
    setup()
    body = starboard.render_highlight("m1", "c1", "u1", "@everyone free pizza, also @[Admins] and @nick look", 0, "", 3)
    assert live_mentions(body) == [], body
    assert "\\@everyone free pizza" in body


def test_a_display_name_is_defanged():
    setup()
    starboard.bot.space.members["u2"] = starboard.bot.space.members["u1"].__class__(member("u2", "evil", display_name="@everyone"), base_permissions=0)
    body = starboard.render_highlight("m1", "c1", "u2", "hi", 0, "", 3)
    assert live_mentions(body) == [], body


def test_a_mention_the_author_already_escaped_stays_escaped():
    setup()
    body = starboard.render_highlight("m1", "c1", "u1", "\\@bob and \\\\@bob and \\\\\\@bob", 0, "", 3)
    assert live_mentions(body) == [], body
    assert body.count("@") == 3


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
