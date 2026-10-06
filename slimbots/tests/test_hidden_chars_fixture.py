"""The python classifier against slim-m's shared hidden-character fixture, code point by code point."""

import json
import os
import sys
from pathlib import Path

import pytest

from slimbots.hidden_chars import is_hidden_char

FIXTURE = Path(__file__).parent / "fixtures" / "hidden_chars.json"
UPSTREAM_ENV = "SLIMM_HIDDEN_CHARS_FIXTURE"


def _load(path):
    fixture = json.loads(Path(path).read_text())
    ranges = [(int(r["from"], 16), int(r["to"], 16)) for r in fixture["hidden"]]
    return fixture, ranges


def test_the_classifier_agrees_with_the_fixture_on_every_code_point():
    fixture, ranges = _load(FIXTURE)
    listed = {code for low, high in ranges for code in range(low, high + 1)}
    wrong = [
        f"U+{code:04X}" for code in range(sys.maxunicode + 1)
        if not 0xD800 <= code <= 0xDFFF and is_hidden_char(chr(code)) != (code in listed)
    ]
    assert not wrong, f"{len(wrong)} code points disagree with the fixture, first: {wrong[:20]}"
    for plain in fixture["plain"]:
        assert not is_hidden_char(chr(int(plain, 16)))


def test_the_vendored_fixture_is_still_slim_ms():
    upstream = os.environ.get(UPSTREAM_ENV)
    if not upstream:
        pytest.skip(f"set {UPSTREAM_ENV} to slim-m's crates/slimm-server/tests/fixtures/hidden_chars.json to check for drift")
    assert json.loads(Path(upstream).read_text()) == json.loads(FIXTURE.read_text())
