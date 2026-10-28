import ast
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import smoke_all as a  # noqa: E402


def exempt():
    return json.loads(a.EXEMPT_FILE.read_text())


def test_every_bot_has_a_manifest_or_a_written_exemption():
    assert a.unaccounted(a.bot_names(), a.manifest_names(), exempt()) == []


def test_an_exemption_never_hides_a_bot_that_has_a_manifest():
    assert set(exempt()) & set(a.manifest_names()) == set()


def test_every_manifest_and_exemption_names_a_real_bot():
    assert set(a.manifest_names()) | set(exempt()) <= set(a.bot_names())


def test_a_bot_with_neither_is_reported():
    assert a.unaccounted(["new"], [], {}) == ["new"]
    assert a.unaccounted(["new"], [], {"new": ""}) == ["new"]
    assert a.unaccounted(["new"], ["new"], {}) == []


def test_prefix_and_channel_kind_are_valid_when_set():
    for name in a.manifest_names():
        data = json.loads((a.SMOKE_DIR / f"{name}.json").read_text())
        assert data.get("channel_kind", "text") in ("text", "voice"), name
        assert isinstance(data.get("prefix", "!"), str) and data.get("prefix", "!").strip(), name


def command_names(bot_dir):
    """Top-level command and group names a bot registers on `bot`, read from its source."""
    names = set()
    for path in bot_dir.glob("*.py"):
        if path.name.startswith("test_"):
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in node.decorator_list:
                call = deco if isinstance(deco, ast.Call) else None
                func = call.func if call else None
                if not (isinstance(func, ast.Attribute) and func.attr in ("command", "group") and isinstance(func.value, ast.Name) and func.value.id == "bot"):
                    continue
                named = [k.value for k in call.keywords if k.arg == "name" and isinstance(k.value, ast.Constant)]
                names.add(named[0].value if named else node.name)
    return names


def typed_commands(name):
    data = json.loads((a.SMOKE_DIR / f"{name}.json").read_text())
    prefix = data.get("prefix", "!")
    return {step["say"][len(prefix):].split()[0] for step in data["steps"] if step["say"].startswith(prefix) and step["say"][len(prefix):].split()}


def test_every_command_a_bot_registers_is_typed_by_its_manifest():
    gaps = {name: sorted(command_names(a.ROOT / "bots" / name) - typed_commands(name)) for name in a.manifest_names()}
    assert {name: missing for name, missing in gaps.items() if missing} == {}
