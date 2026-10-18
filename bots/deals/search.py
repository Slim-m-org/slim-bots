"""Steam storesearch: turns a game name into one app id, or a short list of candidates when the name is ambiguous."""

import re
import urllib.parse
from dataclasses import dataclass

import sources

SEARCH_URL = "https://store.steampowered.com/api/storesearch/?term={term}&cc={cc}&l=en"
MAX_TERM = 100
MAX_CHOICES = 5


@dataclass(frozen=True)
class Game:
    app_id: int
    name: str


@dataclass(frozen=True)
class SearchResult:
    """`best` is set when one hit is clearly the game asked for; otherwise `choices` holds the candidates, empty for no match."""

    best: Game | None = None
    choices: tuple[Game, ...] = ()
    error: str = ""


def normalize(text):
    return re.sub(r"[\W_]+", " ", text.casefold()).strip()


def games_from(data):
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise sources.SourceError("unexpected Steam search answer")
    games = []
    for item in items:
        if isinstance(item, dict) and item.get("type") == "app" and isinstance(item.get("id"), int) and isinstance(item.get("name"), str):
            games.append(Game(item["id"], item["name"]))
    return games


def choose(term, games):
    """A single hit, or the one whose name equals the term (ignoring case and punctuation), is the game; anything else is a pick list."""
    if len(games) == 1:
        return SearchResult(best=games[0])
    wanted = normalize(term)
    exact = [g for g in games if normalize(g.name) == wanted]
    if len(exact) == 1:
        return SearchResult(best=exact[0])
    return SearchResult(choices=tuple(games[:MAX_CHOICES]))


def search_game(transport, term, cc):
    """Never raises: a failed lookup is a SearchResult with `error` set."""
    term = term.strip()[:MAX_TERM]
    try:
        games = games_from(sources.get_json(transport, SEARCH_URL.format(term=urllib.parse.quote(term), cc=cc)))
    except sources.SourceError as err:
        return SearchResult(error=str(err))
    return choose(term, games)
