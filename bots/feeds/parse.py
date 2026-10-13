"""Parses RSS 2.0, RSS 1.0 and Atom into plain entries, refusing DTDs so a hostile feed cannot expand entities."""

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone

MAX_ENTRIES = 100
TAGS = re.compile(r"<[^>]*>")
SPACES = re.compile(r"\s+")


class FeedError(Exception):
    """The body is not a feed we can read; the message is safe to show a member."""


@dataclass
class Entry:
    key: str
    title: str
    link: str
    summary: str = ""
    published: int | None = None


@dataclass
class Parsed:
    title: str
    entries: list = field(default_factory=list)


def local(tag):
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def text_of(element):
    return (element.text or "").strip() if element is not None else ""


def child(element, *names):
    """The first direct child whose namespace-free name is one of `names`, tried in order."""
    for name in names:
        for node in element:
            if local(node.tag) == name:
                return node
    return None


def clean(raw, limit=None):
    """Tags dropped, entities decoded, whitespace collapsed; cut on a word with an ellipsis past `limit`."""
    flat = SPACES.sub(" ", html.unescape(TAGS.sub(" ", html.unescape(raw or "")))).strip()
    if limit and len(flat) > limit:
        flat = flat[: limit - 1].rsplit(" ", 1)[0].rstrip(",.;: ") + "…"
    return flat


def to_millis(raw):
    if not raw:
        return None
    try:
        moment = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            moment = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return int(moment.timestamp() * 1000)


def atom_link(entry):
    best = ""
    for node in entry:
        if local(node.tag) != "link" or not node.get("href"):
            continue
        if node.get("rel") in (None, "alternate"):
            return node.get("href").strip()
        best = best or node.get("href").strip()
    return best


def entry_from(node, atom):
    title = clean(text_of(child(node, "title")))
    link = atom_link(node) if atom else text_of(child(node, "link"))
    ident = text_of(child(node, "id" if atom else "guid"))
    summary_node = child(node, "summary", "content", "description", "encoded", "group")
    if summary_node is not None and local(summary_node.tag) == "group":
        summary_node = child(summary_node, "description")
    when = child(node, "updated", "published", "pubDate", "date")
    key = ident or link or title
    if not key:
        return None
    return Entry(key=key, title=title or link, link=link, summary=clean(text_of(summary_node)), published=to_millis(text_of(when)))


def parse(body):
    """Entries newest-first as the feed lists them; raises FeedError for anything that is not a feed."""
    if b"<!DOCTYPE" in body[:4096].upper() or b"<!ENTITY" in body.upper():
        raise FeedError("feeds with a DOCTYPE or entities are refused")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise FeedError("not valid XML") from None
    kind = local(root.tag)
    if kind not in ("rss", "feed", "RDF"):
        raise FeedError("not an RSS or Atom feed")
    atom = kind == "feed"
    holder = child(root, "channel") if kind == "rss" else root
    nodes = [n for n in (holder if holder is not None else []) if local(n.tag) == ("entry" if atom else "item")]
    if kind == "RDF":
        nodes = [n for n in root if local(n.tag) == "item"]
    entries = [e for e in (entry_from(n, atom) for n in nodes[:MAX_ENTRIES]) if e]
    head = holder if holder is not None else root
    return Parsed(title=clean(text_of(child(head, "title"))), entries=entries)
