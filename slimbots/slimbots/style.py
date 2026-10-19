"""The official look of a slim-bot's output: one palette, one embed shape, one error shape (docs/framework.md, "Official bot style")."""

from __future__ import annotations

import datetime
from typing import Any

from .embeds import Embed

# The app's design tokens as fills: accent, online, away, danger and the neutral rule colour.
INFO = 0x58B4D8
SUCCESS = 0x3FA45B
WARN = 0xC08A2E
ERROR = 0xC0524A
NEUTRAL = 0x6C757E

PALETTE = {"info": INFO, "success": SUCCESS, "warn": WARN, "error": ERROR, "neutral": NEUTRAL}

FOOTER_SEPARATOR = " · "


def footer(*parts: str | None) -> str | None:
    """The footer line: lowercase facts joined by a middle dot, or None when there is nothing to say."""
    kept = [part.strip() for part in parts if part and part.strip()]
    return FOOTER_SEPARATOR.join(kept) or None


def card(
    kind: str = "info", *, title: str | None = None, description: str | None = None, footer_text: str | None = None,
    url: str | None = None, timestamp: int | str | datetime.datetime | None = None, color: int | None = None,
) -> Embed:
    """An embed in the palette colour of `kind` (`info`, `success`, `warn`, `error`, `neutral`); `color` overrides it for a status that has its own."""
    if kind not in PALETTE:
        raise ValueError(f"style kind must be one of {sorted(PALETTE)}, got {kind!r}")
    return Embed(
        title=title, description=description, color=PALETTE[kind] if color is None else color,
        footer=footer_text, url=url, timestamp=timestamp,
    )


def info(title: str | None = None, description: str | None = None, **options: Any) -> Embed:
    return card("info", title=title, description=description, **options)


def success(title: str | None = None, description: str | None = None, **options: Any) -> Embed:
    return card("success", title=title, description=description, **options)


def warn(title: str | None = None, description: str | None = None, **options: Any) -> Embed:
    return card("warn", title=title, description=description, **options)


def error(title: str | None = None, description: str | None = None, **options: Any) -> Embed:
    return card("error", title=title, description=description, **options)


def error_text(what: str, hint: str | None = None) -> str:
    """A failed command's reply: what went wrong in lowercase with a full stop, then an optional hint on how to carry on."""
    text = what.strip().rstrip(".") + "."
    if hint:
        text += " " + hint.strip().rstrip(".") + "."
    return text


def usage_text(*examples: str) -> str:
    """The reply to a command run without what it needs: `try `a`, `b` or `c`.`, each example as code."""
    if not examples:
        raise ValueError("usage_text needs at least one example")
    quoted = [f"`{example}`" for example in examples]
    listed = quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " or " + quoted[-1]
    return f"try {listed}."


def send_kwargs(embed: Embed, fallback: str | None = None) -> dict[str, Any]:
    """`embeds` and `fallback_content` for `client.send`, so an older server shows the card's own text rather than nothing."""
    return {"embeds": [embed.to_wire()], "fallback_content": fallback if fallback is not None else embed.render_fallback()}
