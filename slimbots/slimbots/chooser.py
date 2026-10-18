"""`Chooser`: a message of buttons only its invoker may press, that expires on its own; see docs/framework.md."""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from .components import MAX_LABEL, Button, grid, rows
from .http import ApiError
from .text import clip

if TYPE_CHECKING:
    from .bot import Bot
    from .interactions import Interaction

EXPIRES_SECONDS = 300
EXPIRED_REPLY = "that choice has expired - run {retry} again."


@dataclass
class PickBase:
    """Who may answer one open chooser, and when it was opened; subclass it to carry what the chooser offers."""

    invoker_id: str
    invoker_name: str
    channel_id: str

    def __post_init__(self) -> None:
        self.message_id: str | None = None
        self.created_at = time.monotonic()

    def expired(self, expires: float = EXPIRES_SECONDS) -> bool:
        return time.monotonic() - self.created_at > expires


@dataclass
class Pick(PickBase):
    """Items to choose between and what choosing one does; `on_choose` returns the text the message then shows."""

    items: list[dict[str, Any]]
    query: str
    on_choose: Callable[[dict[str, Any]], Awaitable[str]]
    note: str = ""


class Chooser:
    """Owns the open picks for one command; `prefix` namespaces its buttons and `command` is what a retry types.
    Subclass and override `render` and `handle` for a chooser that pages or changes view; the default offers `Pick.items`."""

    def __init__(
        self, prefix: str, command: str, label: Callable[[dict[str, Any]], str] | None = None, verb: str = "choose", *,
        per_row: int = 2, expires: float = EXPIRES_SECONDS, expired_reply: str = EXPIRED_REPLY,
    ) -> None:
        self.prefix = prefix
        self.command = command
        self.label = label
        self.verb = verb
        self.per_row = per_row
        self.expires = expires
        self.expired_reply = expired_reply
        self._picks: dict[str, Any] = {}

    def register(self, bot: Bot) -> None:
        bot.button(prefix=self.prefix)(self.on_press)

    def retry(self, bot: Bot) -> str:
        return f"`{bot.prefix}{self.command}`"

    def render(self, pick: Any) -> tuple[str, list[dict[str, Any]]]:
        assert self.label is not None, "the default render needs a label function"
        buttons = [Button(clip(self.label(item), MAX_LABEL, "~"), f"{self.prefix}sel:{i}") for i, item in enumerate(pick.items)]
        layout = [*grid(buttons, self.per_row), [Button("Cancel", f"{self.prefix}cancel", style="danger")]]
        text = f'{len(pick.items)} match(es) for "{pick.query}"{pick.note} - {pick.invoker_name}, pick one to {self.verb}.'
        return text, rows(*layout)

    async def open(self, bot: Bot, pick: Any, *, reply_to_id: str | None = None) -> Any:
        """Posts the chooser, remembers it, and closes it with a retry hint once `expires` passes."""
        assert bot.client is not None
        text, layout = self.render(pick)
        message = await bot.client.send(pick.channel_id, text, reply_to_id=reply_to_id, components=layout)
        pick.message_id = message.id
        self._picks[message.id] = pick
        bot.background(self.expire(bot, pick), name=f"{self.prefix}pick-{message.id}")
        return pick

    async def expire(self, bot: Bot, pick: Any, delay: float | None = None) -> None:
        await asyncio.sleep(self.expires if delay is None else delay)
        if self._picks.get(pick.message_id) is pick:
            await self.close(bot, pick, f"timed out - {self.retry(bot)} again to retry.")

    async def close(self, bot: Bot, pick: Any, text: str) -> None:
        """Forgets the pick and replaces its message with `text`, buttons gone."""
        assert bot.client is not None
        self._picks.pop(pick.message_id, None)
        with contextlib.suppress(ApiError):
            await bot.client.edit_message(pick.channel_id, pick.message_id, text)
            await bot.client.edit_components(pick.channel_id, pick.message_id, [])

    async def redraw(self, bot: Bot, pick: Any) -> None:
        assert bot.client is not None
        text, layout = self.render(pick)
        with contextlib.suppress(ApiError):
            await bot.client.edit_message(pick.channel_id, pick.message_id, text)
            await bot.client.edit_components(pick.channel_id, pick.message_id, layout)

    async def guard(self, interaction: Interaction) -> Any:
        """The pick this press belongs to, or None after answering why not (gone or expired, or pressed by someone else)."""
        bot, retry = interaction.bot, self.retry(interaction.bot)
        pick = self._picks.get(interaction.message_id) if interaction.message_id else None
        if pick is None or pick.expired(self.expires):
            if pick is not None:
                await self.close(bot, pick, f"timed out - {retry} again to retry.")
            with contextlib.suppress(ApiError):
                await interaction.reply_ephemeral(self.expired_reply.format(retry=retry))
            return None
        if interaction.user_id != pick.invoker_id:
            with contextlib.suppress(ApiError):
                await interaction.reply_ephemeral(f"only {pick.invoker_name} can choose here - {retry} starts your own.")
            return None
        return pick

    async def on_press(self, interaction: Interaction) -> None:
        pick = await self.guard(interaction)
        if pick is not None:
            await self.handle(interaction, pick, interaction.custom_id[len(self.prefix):])

    async def handle(self, interaction: Interaction, pick: Any, action: str) -> None:
        """The default: `sel:<index>` runs `pick.on_choose` on that item and `cancel` closes; anything else is acknowledged and ignored."""
        bot = interaction.bot
        index = int(action[4:]) if action.startswith("sel:") and action[4:].isdigit() else None
        if action != "cancel" and (index is None or index >= len(pick.items)):
            await interaction.ack()
            return
        self._picks.pop(pick.message_id, None)
        await interaction.ack()
        if action == "cancel":
            await self.close(bot, pick, "cancelled.")
            return
        assert index is not None
        try:
            outcome = await pick.on_choose(pick.items[index])
        except Exception as err:
            print(f"chooser `{self.prefix}` could not act on a pick: {err}", file=sys.stderr)
            outcome = f"could not {self.verb} that - {self.retry(bot)} again to retry."
        await self.close(bot, pick, outcome)
