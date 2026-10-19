"""Fixed choices for a command's arguments (the server's command options): declared once, advertised, and enforced on invoke."""

from __future__ import annotations

import inspect
from typing import Any, Mapping, Sequence

from .hidden_chars import is_hidden_char

MAX_OPTIONS = 5
MAX_CHOICES = 25
MAX_CHOICE_LEN = 100


def validate_choices(params: Sequence[inspect.Parameter], choices: Mapping[str, Sequence[str]] | None) -> dict[str, tuple[str, ...]]:
    """`choices` as a checked dict; a name that is no parameter, or a choice the server would refuse, raises at declaration."""
    checked: dict[str, tuple[str, ...]] = {}
    names = [p.name for p in params]
    for name, values in (choices or {}).items():
        if name not in names:
            raise ValueError(f"choices names `{name}`, which is not a parameter of the handler")
        values = tuple(values)
        folded = {v.lower() for v in values}
        if not values or len(values) > MAX_CHOICES or len(folded) != len(values):
            raise ValueError(f"`{name}` needs 1 to {MAX_CHOICES} distinct choices")
        for value in values:
            if not value or len(value) > MAX_CHOICE_LEN or any(c.isspace() for c in value) or any(is_hidden_char(c) for c in value):
                raise ValueError(f"choice {value!r} of `{name}` must be 1 to {MAX_CHOICE_LEN} characters with no whitespace")
        checked[name] = values
    if checked and len(params) > MAX_OPTIONS:
        raise ValueError(f"a command with choices may take at most {MAX_OPTIONS} parameters")
    return checked


def canonical_choice(token: str, values: Sequence[str]) -> str | None:
    """The declared spelling of `token`, matched ignoring case, or None when it is not one of `values`."""
    folded = token.lower()
    return next((v for v in values if v.lower() == folded), None)


def option_entries(params: Sequence[inspect.Parameter], choices: Mapping[str, tuple[str, ...]], no_default: Any) -> list[dict[str, Any]]:
    """The wire `options` of a command: one per parameter, in order, so the composer can tell which argument is being typed."""
    if not choices:
        return []
    return [
        {"name": p.name, "required": p.default is no_default, "choices": list(choices.get(p.name, ()))}
        for p in params
    ]
