"""check_hygiene refuses every character slimbots.hidden_chars treats as hidden, not only the zero-width ones."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import check_hygiene  # noqa: E402

HIDDEN_SAMPLES = [
    "\u00ad", "\u034f", "\u061c", "\u115f", "\u17b4", "\u180e", "\u200b", "\u200f", "\u2028", "\u2029",
    "\u202a", "\u202d", "\u202e", "\u2060", "\u2066", "\u2069", "\u2800", "\u3164", "\ufeff", "\uffa0",
    "\ufff9", "\U000e0001", "\U000e0041", "\x00", "\x1b", "\x7f",
]


def write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


@pytest.mark.parametrize("hidden", HIDDEN_SAMPLES, ids=lambda ch: f"U+{ord(ch):04X}")
def test_a_hidden_character_fails_the_gate(tmp_path, hidden):
    write(tmp_path, "bot.py", f"x = 1\nname = 'adm{hidden}in'\n")
    assert check_hygiene.check(tmp_path) == [f"bot.py:2: invisible character U+{ord(hidden):04X}, write it as a \\u escape"]


def test_a_bidi_override_in_a_readme_fails_the_gate(tmp_path):
    write(tmp_path, "README.md", "# bot\n\nrun it \u202eesrever\n")
    assert check_hygiene.check(tmp_path) == ["README.md:3: invisible character U+202E, write it as a \\u escape"]


def test_a_line_separator_does_not_shift_later_line_numbers(tmp_path):
    write(tmp_path, "notes.txt", "a\u2028b\nc\u202ed\n")
    assert check_hygiene.check(tmp_path) == [
        "notes.txt:1: invisible character U+2028, write it as a \\u escape",
        "notes.txt:2: invisible character U+202E, write it as a \\u escape",
    ]


def test_tabs_newlines_and_escapes_pass(tmp_path):
    write(tmp_path, "bot.py", "x = '\\u202e'\nif x:\n\ty = 1\n")
    assert check_hygiene.check(tmp_path) == []


def test_the_repo_itself_passes():
    assert check_hygiene.check() == []
