"""Publishing a bot's prefix and commands (decision 0031); see docs/framework.md."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from .hidden_chars import is_hidden_char
from .http import ApiError

if TYPE_CHECKING:
    from .commands import Command
    from .http import AsyncClient

log = logging.getLogger("slimbots")

DROPPED_HEADER = "x-slimm-dropped-commands"
MAX_NAME_LEN = 32
_VALID_NAME = re.compile(r"[A-Za-z0-9_-]+")


class RegistrationRejected(ApiError):
    """The server answered a registration with 400: the same body is refused every time, so retrying only spins."""

    def __init__(self, route: str, cause: ApiError) -> None:
        super().__init__(cause.status, cause.body)
        self.route = route


class RegistrationConflict(ApiError):
    """The server answered 409: another bot holds this prefix and command name, or the prefix is not the assigned one."""

    def __init__(self, route: str, cause: ApiError) -> None:
        super().__init__(cause.status, cause.body)
        self.route = route


MAX_REGISTERED_COMMANDS = 50
MAX_DESCRIPTION_LEN = 100
MAX_USAGE_LEN = 80


def registration_body(prefix: str, commands: list[Command]) -> dict[str, Any]:
    """Every registered name and alias as its own composer entry; a group still counts as just one.
    A name the server would refuse is left out with a warning, so one bad alias cannot stop the whole registration."""
    entries = []
    seen: set[str] = set()
    for command in commands:
        for name in command.names:
            if len(entries) >= MAX_REGISTERED_COMMANDS:
                break
            if not _registrable(name, seen):
                continue
            seen.add(name.lower())
            entries.append(_entry(command, name))
    return {"prefix": prefix, "commands": entries}


def _registrable(name: str, seen: set[str]) -> bool:
    if not name or len(name) > MAX_NAME_LEN or not _VALID_NAME.fullmatch(name):
        log.warning("command name %r is not registered with the server: names are 1-%d letters, digits, - or _", name, MAX_NAME_LEN)
        return False
    if name.lower() in seen:
        log.warning("command name %r is not registered with the server: another name differs from it only by case", name)
        return False
    return True


def clean_text(text: str, limit: int) -> str:
    """Whitespace collapsed to single spaces and hidden characters removed, as the server's label checks require."""
    visible = "".join(" " if char.isspace() else char for char in text if char.isspace() or not is_hidden_char(char))
    return " ".join(visible.split())[:limit].rstrip()


def _entry(command: Command, name: str) -> dict[str, Any]:
    description = clean_text(command.help or "", MAX_DESCRIPTION_LEN) or clean_text(command.name, MAX_DESCRIPTION_LEN) or name
    entry: dict[str, Any] = {"name": name, "description": description}
    usage = clean_text(command.usage, MAX_USAGE_LEN) if command.usage else ""
    if usage:
        entry["usage"] = usage
    if command.requires is not None:
        entry["permission"] = int(command.requires)
    if command.options:
        entry["options"] = command.options
    return entry


def dropped_names(headers: Mapping[str, str]) -> list[str]:
    """The command names the server left out of a registration, from its x-slimm-dropped-commands header."""
    return [name.strip() for name in headers.get(DROPPED_HEADER, "").split(",") if name.strip()]


async def register_commands(
    client: AsyncClient, *, prefix: str, commands: list[Command], on_dropped: Callable[[list[str]], None] | None = None,
) -> bool:
    """PUT /bots/commands with the bot's whole set; returns False, quietly, against a server too old to have the route.
    Names the server gave to another bot are logged as a warning and passed to `on_dropped` (decision 0069)."""

    def note_headers(headers: Mapping[str, str]) -> None:
        dropped = dropped_names(headers)
        if not dropped:
            return
        log.warning("the server left these commands out because another bot holds them under %r: %s", prefix, ", ".join(dropped))
        if on_dropped is not None:
            on_dropped(dropped)

    try:
        await client.call("PUT", "/bots/commands", registration_body(prefix, commands), on_headers=note_headers)
        return True
    except ApiError as err:
        if err.status in (404, 405):
            return False
        if err.status == 400:
            raise RegistrationRejected("/bots/commands", err) from err
        if err.status == 409:
            raise RegistrationConflict("/bots/commands", err) from err
        raise
