"""Buttons for a bot's message (slim-m decision 0039): `Button` and `rows()`, the wire shape `send(components=...)` takes."""

from __future__ import annotations

from typing import Any, Sequence, Union

from .hidden_chars import has_hidden_char

STYLES = ("primary", "secondary", "danger", "link")
MAX_ROWS = 5
MAX_BUTTONS_PER_ROW = 5
MAX_LABEL = 80
MAX_CUSTOM_ID = 100


class Button:
    """One button; a link button takes a `url` and never reaches the bot, any other takes a `custom_id`."""

    def __init__(
        self, label: str, custom_id: str | None = None, *, style: str = "secondary", url: str | None = None,
        disabled: bool = False,
    ) -> None:
        if style not in STYLES:
            raise ValueError(f"button style must be one of {STYLES}, not {style!r}")
        label = label.strip()
        if not label or len(label) > MAX_LABEL:
            raise ValueError(f"a button label is 1 to {MAX_LABEL} characters")
        if has_hidden_char(label) or has_hidden_char(custom_id or ""):
            raise ValueError("a button label or custom_id cannot hold control or invisible characters")
        if style == "link":
            if not url or custom_id:
                raise ValueError("a link button takes a url and no custom_id")
        elif not custom_id or url or len(custom_id) > MAX_CUSTOM_ID:
            raise ValueError(f"a button takes a custom_id of 1 to {MAX_CUSTOM_ID} characters and no url")
        self.label = label
        self.custom_id = custom_id
        self.style = style
        self.url = url
        self.disabled = disabled

    @classmethod
    def link(cls, label: str, url: str) -> Button:
        return cls(label, style="link", url=url)

    def to_wire(self) -> dict[str, Any]:
        wire: dict[str, Any] = {"label": self.label, "style": self.style}
        if self.custom_id:
            wire["custom_id"] = self.custom_id
        if self.url:
            wire["url"] = self.url
        if self.disabled:
            wire["disabled"] = True
        return wire


Rows = Sequence[Union[Sequence[Button], "dict[str, Any]"]]


def rows(*layout: Sequence[Button] | dict[str, Any]) -> list[dict[str, Any]]:
    """`rows([Button("Hit", "hit"), Button("Stand", "stand")], [...])` as the `components` a send takes."""
    return to_wire(layout)


def to_wire(layout: Rows) -> list[dict[str, Any]]:
    """Accepts rows of `Button`s or ready-made wire dicts; the server enforces the same caps on either."""
    if len(layout) > MAX_ROWS:
        raise ValueError(f"a message holds at most {MAX_ROWS} rows of buttons")
    wire: list[dict[str, Any]] = [row if isinstance(row, dict) else {"buttons": [b.to_wire() for b in row]} for row in layout]
    seen: set[str] = set()
    for row in wire:
        buttons = row.get("buttons") or []
        if not buttons:
            raise ValueError("a row of buttons holds at least one button")
        if len(buttons) > MAX_BUTTONS_PER_ROW:
            raise ValueError(f"a row holds at most {MAX_BUTTONS_PER_ROW} buttons")
        for custom_id in (b.get("custom_id") for b in buttons if b.get("custom_id")):
            if custom_id in seen:
                raise ValueError(f"custom_id {custom_id!r} is used twice in one message")
            seen.add(custom_id)
    return wire
