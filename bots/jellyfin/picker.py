"""Button pickers for `!watch`: the matches, a series' seasons and episodes, and the resume-or-start-over question."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from slimbots import ApiError, Button, rows
from slimbots.chooser import EXPIRES_SECONDS, Chooser, PickBase
from slimbots.components import MAX_LABEL, grid
from slimbots.media import format_hms
from slimbots.text import clip

import accounts
import jellyfin_core

ID_PREFIX = "jfp:"
PAGE_SIZE = 10
PER_ROW = 5
MAX_MATCHES = 5


@dataclass
class Pick(PickBase):
    """One open chooser message and where the invoker is in it."""

    on_choose: Callable[[Any], Awaitable[None]]
    view: str = "results"
    items: list[dict[str, Any]] = field(default_factory=list)
    query: str = ""
    series: dict[str, Any] | None = None
    results: list[dict[str, Any]] = field(default_factory=list)
    seasons: list[dict[str, Any]] = field(default_factory=list)
    page: int = 0
    resume_seconds: float = 0.0
    title: str = ""


def label_for(item):
    name = item.get("Name") or "Untitled"
    kind = item.get("Type")
    if kind == "Episode":
        season, number = item.get("ParentIndexNumber"), item.get("IndexNumber")
        prefix = f"S{season}E{number} " if season is not None and number is not None else ""
        series = f"{item['SeriesName']} - " if item.get("SeriesName") and not prefix else ""
        return clip(f"{series}{prefix}{name}", MAX_LABEL, "~")
    year = f" ({item['ProductionYear']})" if item.get("ProductionYear") else ""
    return clip(f"{name}{year}" + (" - series" if kind == "Series" else ""), MAX_LABEL, "~")


def _page_slice(pick):
    start = pick.page * PAGE_SIZE
    return start, pick.items[start:start + PAGE_SIZE]


def _item_rows(pick):
    start, shown = _page_slice(pick)
    return grid([Button(label_for(item), f"{ID_PREFIX}sel:{start + i}") for i, item in enumerate(shown)], PER_ROW)


def _nav_row(pick):
    nav = []
    if pick.view in ("seasons", "episodes"):
        nav.append(Button("Back", f"{ID_PREFIX}back"))
    if pick.page > 0:
        nav.append(Button("Previous", f"{ID_PREFIX}prev"))
    if (pick.page + 1) * PAGE_SIZE < len(pick.items):
        nav.append(Button("Next page", f"{ID_PREFIX}next"))
    nav.append(Button("Cancel", f"{ID_PREFIX}cancel", style="danger"))
    return nav


def render(pick):
    """The message text and button rows for wherever the picker is."""
    if pick.view == "resume":
        text = f"**{pick.title}** is at {format_hms(pick.resume_seconds)}."
        return text, rows([
            Button(f"Resume at {format_hms(pick.resume_seconds)}", f"{ID_PREFIX}resume", style="primary"),
            Button("Start over", f"{ID_PREFIX}restart"), Button("Cancel", f"{ID_PREFIX}cancel", style="danger"),
        ])
    heading = {
        "results": f'{len(pick.items)} matches for "{pick.query}"',
        "seasons": f"**{(pick.series or {}).get('Name', 'Series')}** - pick a season",
        "episodes": f"**{pick.title}** - pick an episode",
    }[pick.view]
    pages = f" (page {pick.page + 1} of {-(-len(pick.items) // PAGE_SIZE)})" if len(pick.items) > PAGE_SIZE else ""
    return f"{heading}{pages} - {pick.invoker_name} is choosing.", rows(*_item_rows(pick), _nav_row(pick))


class WatchChooser(Chooser):
    """The `!watch` chooser: matches, a series' seasons and episodes, and the resume question, all in one message."""

    def render(self, pick):
        return render(pick)

    async def handle(self, interaction, pick, action):
        bot = interaction.bot
        await interaction.ack()
        if action.startswith("sel:"):
            await _select(bot, pick, int(action[4:]))
        elif action in ("resume", "restart"):
            await self.close(bot, pick, "starting...")
            await pick.on_choose(pick.resume_seconds if action == "resume" else 0.0)
        elif action == "cancel":
            await self.close(bot, pick, "cancelled.")
        elif action == "back":
            await _back(bot, pick)
        elif action in ("prev", "next"):
            pick.page = max(0, pick.page + (1 if action == "next" else -1))
            await self.redraw(bot, pick)


CHOOSER = WatchChooser(ID_PREFIX, "watch", expires=EXPIRES_SECONDS, expired_reply="that choice has expired - {retry} again.")


async def _open_series(bot, pick, series):
    user_id = await accounts.user_for(bot, pick.invoker_id)
    seasons = await jellyfin_core.series_seasons(series["Id"], user_id)
    pick.series, pick.title, pick.page, pick.seasons = series, series.get("Name") or "Series", 0, seasons
    if len(seasons) == 1:
        await _open_season(bot, pick, seasons[0])
    else:
        pick.view, pick.items = "seasons", seasons


async def _open_season(bot, pick, season):
    user_id = await accounts.user_for(bot, pick.invoker_id)
    pick.items = await jellyfin_core.season_episodes(pick.series["Id"], season["Id"], user_id)
    pick.view, pick.page = "episodes", 0
    pick.title = f"{(pick.series or {}).get('Name', 'Series')}, {season.get('Name') or 'season'}"


async def _select(bot, pick, index):
    item = pick.items[index] if 0 <= index < len(pick.items) else None
    if item is None:
        return
    if pick.view == "results" and item.get("Type") == "Series":
        pick.results = pick.items
        await _open_series(bot, pick, item)
    elif pick.view == "seasons":
        await _open_season(bot, pick, item)
    else:
        await CHOOSER.close(bot, pick, f"starting **{item.get('Name')}**...")
        await pick.on_choose(item)
        return
    await CHOOSER.redraw(bot, pick)


async def _back(bot, pick):
    """Episodes go back to the seasons (or the matches, for a one-season series), seasons to the matches."""
    if pick.view == "episodes" and len(pick.seasons) > 1:
        pick.view, pick.items = "seasons", pick.seasons
    else:
        pick.view, pick.items = "results", pick.results
    pick.page = 0
    await CHOOSER.redraw(bot, pick)
