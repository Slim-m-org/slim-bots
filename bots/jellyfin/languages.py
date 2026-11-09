"""Language names and ISO 639 codes, so `ja`, `jpn` and `Japanese` all name one language; see README.md."""

from __future__ import annotations

# (639-1, 639-2/B, 639-2/T when it differs, name). Jellyfin reports either 639-2 form, depending on how a file was tagged.
_LANGUAGES = (
    ("en", "eng", None, "English"), ("ja", "jpn", None, "Japanese"), ("ko", "kor", None, "Korean"),
    ("zh", "chi", "zho", "Chinese"), ("es", "spa", None, "Spanish"), ("pt", "por", None, "Portuguese"),
    ("fr", "fre", "fra", "French"), ("de", "ger", "deu", "German"), ("it", "ita", None, "Italian"),
    ("ru", "rus", None, "Russian"), ("sv", "swe", None, "Swedish"), ("no", "nor", None, "Norwegian"),
    ("da", "dan", None, "Danish"), ("fi", "fin", None, "Finnish"), ("nl", "dut", "nld", "Dutch"),
    ("pl", "pol", None, "Polish"), ("cs", "cze", "ces", "Czech"), ("tr", "tur", None, "Turkish"),
    ("ar", "ara", None, "Arabic"), ("he", "heb", None, "Hebrew"), ("hi", "hin", None, "Hindi"),
    ("th", "tha", None, "Thai"), ("vi", "vie", None, "Vietnamese"), ("id", "ind", None, "Indonesian"),
    ("ms", "may", "msa", "Malay"), ("uk", "ukr", None, "Ukrainian"), ("el", "gre", "ell", "Greek"),
    ("hu", "hun", None, "Hungarian"), ("ro", "rum", "ron", "Romanian"), ("bg", "bul", None, "Bulgarian"),
    ("hr", "hrv", None, "Croatian"), ("sr", "srp", None, "Serbian"), ("sk", "slo", "slk", "Slovak"),
    ("sl", "slv", None, "Slovenian"), ("ca", "cat", None, "Catalan"), ("fa", "per", "fas", "Persian"),
    ("tl", "tgl", None, "Tagalog"), ("ta", "tam", None, "Tamil"), ("bn", "ben", None, "Bengali"),
    ("is", "ice", "isl", "Icelandic"), ("lt", "lit", None, "Lithuanian"), ("lv", "lav", None, "Latvian"),
    ("et", "est", None, "Estonian"),
)
_NAMES = {row[3].lower(): row[1] for row in _LANGUAGES}
_CODES = {code: row[1] for row in _LANGUAGES for code in row[:3] if code}
_DISPLAY = {row[1]: row[3] for row in _LANGUAGES}
UNDETERMINED = ("und", "zxx", "mul", "mis")
MIN_PREFIX = 3


def language_key(text):
    """The one code (639-2/B) a code or name stands for, or None for blank, undetermined or unknown text.
    A three-letter code this table lacks stands for itself, so an unlisted language still matches its own tracks."""
    word = (text or "").strip().lower().replace("_", "-").split("-")[0]
    if not word or word in UNDETERMINED:
        return None
    if word in _CODES:
        return _CODES[word]
    if word in _NAMES:
        return _NAMES[word]
    if len(word) >= MIN_PREFIX:
        prefixed = {key for name, key in _NAMES.items() if name.startswith(word)}
        if len(prefixed) == 1:
            return prefixed.pop()
    return word if len(word) == 3 and word.isalpha() else None


def language_name(key):
    """A language's English name for a message, or its code in capitals when the table lacks it."""
    return _DISPLAY.get(key) or str(key).upper()
