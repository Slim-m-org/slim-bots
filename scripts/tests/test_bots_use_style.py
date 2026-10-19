"""Every bot builds its embeds through `slimbots.style`; a raw `Embed(` or `.to_wire()` in a bot fails here."""

import io
import tokenize
from pathlib import Path

BOTS = Path(__file__).resolve().parents[2] / "bots"
FORBIDDEN = ("Embed", "to_wire")


def code_names(source: str) -> list[tuple[str, int]]:
    """Name tokens only, so a comment, a docstring or a string that mentions `Embed` cannot trip it."""
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    return [(tok.string, tok.start[0]) for tok in tokens if tok.type == tokenize.NAME]


def bot_sources():
    for path in sorted(BOTS.glob("*/*.py")):
        if path.name.startswith("test_") or "fixtures" in path.parts or path.parent.name == "_template":
            continue
        yield path


def test_no_bot_builds_a_raw_embed():
    offenders = [
        f"{path.relative_to(BOTS)}:{line} uses {name}"
        for path in bot_sources()
        for name, line in code_names(path.read_text(encoding="utf-8"))
        if name in FORBIDDEN
    ]
    assert not offenders, "build embeds with slimbots.style, not Embed/to_wire:\n" + "\n".join(offenders)


def test_the_scrubber_ignores_comments_and_strings():
    assert code_names('x = "Embed("  # Embed\n"""Embed"""\n') == [("x", 1)]
