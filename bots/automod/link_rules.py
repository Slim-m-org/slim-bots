"""Finding links and words the way a reader sees them; see README.md."""

import re

from slimbots.hidden_chars import is_hidden_char


def strip_punctuation(token):
    return token.strip("<>()[]{}\"'.,;:!?")


# Where the client starts a link: any "http(s)://" not glued to a word character, up to the next space; a hidden character is not a word character.
_LINK_START = re.compile(r"(?<![A-Za-z0-9_])(?:https?://|www\.)[^\s]+", re.IGNORECASE)


def visible_text(text):
    """`text` without the characters a reader never sees, so one cannot split a link or a listed word."""
    return "".join(char for char in text if not is_hidden_char(char))


def link_domains(text):
    """Host of every http(s):// or www. link in `text`, lowercased, without a leading `www.`, a port or a trailing dot."""
    hosts = []
    for match in _LINK_START.finditer(text):
        rest = visible_text(match.group()).split("://", 1)[-1]
        host = rest.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0].rsplit("@", 1)[-1].split(":", 1)[0].lower()
        host = strip_punctuation(host)
        if host:
            hosts.append(host[4:] if host.startswith("www.") else host)
    return hosts
