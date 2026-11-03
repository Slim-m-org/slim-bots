"""A cheap first guard on what members ask to draw; the model's own refusal and the picture checks come after it."""

import re

# Whole-word or whole-phrase matches only, so "essex" or "scunthorpe" are not caught.
_WORDS = (
    "naked", "nude", "nudes", "nudity", "nsfw", "porn", "porno", "pornographic", "sex", "sexy", "sexual", "sexually", "erotic", "erotica", "topless",
    "bottomless", "lingerie", "undressed", "unclothed", "stripping", "stripper", "orgasm", "genitals", "genitalia", "penis", "vagina", "breasts",
    "boobs", "tits", "nipples", "butt naked", "hentai", "fetish", "bdsm", "xxx", "explicit", "gore", "gory", "beheading", "raped", "rapist",
    "raping", "incest", "loli", "lolicon", "cum", "cumshot", "dildo", "blowjob", "handjob", "fuck", "fucking",
)
_STEMS = ("masturbat", "molest")
_PATTERN = re.compile(
    r"(?<![a-z0-9])(?:" + "|".join(re.escape(word) for word in _WORDS) + "|" + "|".join(re.escape(stem) + "[a-z]*" for stem in _STEMS) + r")(?![a-z0-9])",
    re.IGNORECASE,
)
REFUSAL = "I won't draw that. I keep it clean: nothing sexual, naked or graphic."


def is_explicit(text):
    """True when the request names something sexual, naked or graphic."""
    return bool(_PATTERN.search(text or ""))
