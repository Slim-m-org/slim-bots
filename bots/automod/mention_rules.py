"""Counting mentions the way the server resolves them; see README.md."""

import re

# The server's fence whitespace (code_fences.rs is_fence_space), the client regex `\s`.
_FENCE_SPACE = frozenset("\t\n\x0b\x0c\r \xa0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff" + "".join(map(chr, range(0x2000, 0x200B))))
_FENCE_OPEN = re.compile(r"```[A-Za-z0-9_+#-]*")
# An `@` that opens a user mention, or a role mention whose bracketed name is captured.
_MENTION = re.compile(r"@(?=[A-Za-z0-9_]|(\[[^\]\n]*\]))")


def _only_fence_space(rest):
    return all(char in _FENCE_SPACE for char in rest)


def _opens_fence(line):
    match = _FENCE_OPEN.match(line)
    return match is not None and _only_fence_space(line[match.end():])


def _closes_fence(line):
    return line.startswith("```") and _only_fence_space(line[3:])


def _without_inline_code(line):
    """`line` with each non-empty backtick pair on it replaced by a space, as the server's `without_inline_code`."""
    out, rest = [], line
    while (open_at := rest.find("`")) >= 0:
        out.append(rest[:open_at])
        after = rest[open_at + 1:]
        close = after.find("`")
        if close > 0:
            out.append(" ")
            rest = after[close + 1:]
        else:
            out.append("`")
            rest = after
    return "".join(out) + rest


def prose_outside_code(text):
    """`text` without fenced blocks or inline code, the part the server resolves mentions in (code_fences.rs)."""
    lines = text.split("\n")
    prose, i = [], 0
    while i < len(lines):
        if _opens_fence(lines[i]):
            close = next((j for j in range(i + 1, len(lines)) if _closes_fence(lines[j])), None)
            if close is not None:
                i = close + 1
                continue
        prose.append(_without_inline_code(lines[i]))
        i += 1
    return "\n".join(prose)


def _escaped(text, at):
    start = at
    while start > 0 and text[start - 1] == "\\":
        start -= 1
    return (at - start) % 2 == 1


def mention_count(text):
    """Every unescaped `@name` and `@[Role]` in the prose, glued or not, as the server's mention_grammar.rs reads them."""
    prose = prose_outside_code(text)
    return sum(
        1 for match in _MENTION.finditer(prose)
        if not _escaped(prose, match.start()) and (match.group(1) is None or match.group(1)[1:-1].strip())
    )
