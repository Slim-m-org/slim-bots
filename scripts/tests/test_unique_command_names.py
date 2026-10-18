"""No two shipped bots on one prefix register the same top-level command name or alias."""

import ast
from collections import defaultdict
from pathlib import Path

# Known clash the owner has to settle: give one bot another prefix in Space settings, or rename a command.
KNOWN_CLASHES = {"!stop": ["jellyfin", "pelican"]}

BOTS = Path(__file__).resolve().parents[2] / "bots"


def _literal(node: ast.AST | None):
    return node.value if isinstance(node, ast.Constant) else None


def _prefix(tree: ast.Module) -> str:
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "Bot":
            for kw in node.keywords:
                if kw.arg == "prefix" and isinstance(_literal(kw.value), str):
                    return _literal(kw.value)
            if node.args and isinstance(_literal(node.args[0]), str):
                return _literal(node.args[0])
    return "!"


def _top_level_names(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for deco in node.decorator_list:
            call = deco if isinstance(deco, ast.Call) else None
            target = call.func if call else deco
            on_bot = isinstance(target, ast.Attribute) and target.attr in ("command", "group") \
                and getattr(target.value, "id", None) == "bot"
            if not on_bot:
                continue
            kws = {kw.arg: kw.value for kw in call.keywords} if call else {}
            names.add(_literal(kws.get("name")) or node.name)
            aliases = kws.get("aliases")
            if isinstance(aliases, ast.List):
                names.update(v for v in map(_literal, aliases.elts) if isinstance(v, str))
    return names


def test_no_two_bots_on_one_prefix_share_a_top_level_command():
    owners: dict[tuple[str, str], set[str]] = defaultdict(set)
    for bot_dir in sorted(p for p in BOTS.iterdir() if p.is_dir()):
        for source in bot_dir.glob("*.py"):
            if source.name.startswith("test_"):
                continue
            tree = ast.parse(source.read_text())
            prefix = _prefix(tree) if source.name == "bot.py" else _prefix(ast.parse((bot_dir / "bot.py").read_text())) \
                if (bot_dir / "bot.py").exists() else "!"
            for name in _top_level_names(tree):
                owners[(prefix, name.lower())].add(bot_dir.name)
    clashes = {f"{p}{n}": sorted(b) for (p, n), b in owners.items() if len(b) > 1}
    assert clashes == KNOWN_CLASHES, f"bots registering the same trigger: {clashes}"
