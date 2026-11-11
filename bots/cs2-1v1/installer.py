"""Installs Metamod:Source, CounterStrikeSharp and the alerena arena plugin into the CS2 install volume; see README.md.
Runs inside the game container before the server starts (stdlib only), and again as `patch` from pre.sh after every game update."""

import hashlib
import io
import json
import os
import sys
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass

METAMOD_LINE = "\t\t\tGame\tcsgo/addons/metamod\n"
GAME_LINE = "Game\tcsgo"
STATE_FILE = "slimm-arena/installed.json"
PRE_HOOK = 'python3 "$STEAMAPPDIR/slimm-installer.py" patch || exit 1\n'


@dataclass(frozen=True)
class Package:
    name: str
    url: str
    sha256: str
    dest: str
    marker: str


PACKAGES = [
    Package(
        "metamod-2.0.0-git1490",
        "https://mms.alliedmods.net/mmsdrop/2.0/mmsource-2.0.0-git1490-linux.tar.gz",
        "9295101ff82de19f4d9e8b3cbd6cf521443932302cab32bd736f8f674b3c7fe4",
        "game/csgo",
        "addons/metamod/bin/linuxsteamrt64/metamod.2.cs2.so",
    ),
    Package(
        "counterstrikesharp-1.0.379",
        "https://github.com/roflmuffin/CounterStrikeSharp/releases/download/v1.0.379/counterstrikesharp-with-runtime-linux-1.0.379.zip",
        "bd7e52265346a66a3106934c1f1085b642ec5560b7f070a9b10be510341d583a",
        "game/csgo",
        "addons/counterstrikesharp/bin/linuxsteamrt64/counterstrikesharp.so",
    ),
    Package(
        "alerena-3.0.0-rc.2",
        "https://github.com/ale-devl/alerena/releases/download/v3.0.0-rc.2/alerena.zip",
        "800037a96566178b4937b1199e6134cf17bf207c3788db158cf2b91145de06b6",
        "game/csgo/addons/counterstrikesharp",
        "plugins/alerena/alerena.dll",
    ),
]


class InstallError(Exception):
    """A download that does not match its pin, an archive that escapes its folder, or a gameinfo.gi without the expected line."""


def download(url):
    request = urllib.request.Request(url, headers={"User-Agent": "slimm-cs2-1v1-installer"})  # noqa: S310 - fixed https URLs
    with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
        return response.read()


def safe_target(dest, member):
    """The path a member extracts to, refusing anything that would land outside `dest`."""
    target = os.path.realpath(os.path.join(dest, member))
    if os.path.commonpath([os.path.realpath(dest), target]) != os.path.realpath(dest):
        raise InstallError(f"archive member {member!r} escapes its folder")
    return target


def extract(data, dest):
    """Unpacks a zip or tar.gz held in memory into `dest`, files only, with every path checked first."""
    os.makedirs(dest, exist_ok=True)
    if data[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = archive.infolist()
            for info in members:
                safe_target(dest, info.filename)
            archive.extractall(dest)
    else:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            members = archive.getmembers()
            for member in members:
                safe_target(dest, member.name)
                if not (member.isdir() or member.isfile()):
                    raise InstallError(f"archive member {member.name!r} is not a plain file")
            archive.extractall(dest, filter="data")
    for folder, _, files in os.walk(dest):
        for name in files:
            os.chmod(os.path.join(folder, name), 0o755 if name.endswith(".so") else 0o644)


def read_state(root):
    try:
        with open(os.path.join(root, STATE_FILE), encoding="utf8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return {}


def write_state(root, state):
    path = os.path.join(root, STATE_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf8") as handle:
        json.dump(state, handle, sort_keys=True)


def is_current(root, package, state):
    return state.get(package.name) == package.sha256 and os.path.exists(os.path.join(root, package.dest, package.marker))


def ensure(root, packages=None, fetch=download):
    """Downloads and unpacks every package whose pinned hash is not already installed; returns the names it installed."""
    packages = PACKAGES if packages is None else packages
    state = read_state(root)
    installed = []
    for package in packages:
        if is_current(root, package, state):
            continue
        data = fetch(package.url)
        digest = hashlib.sha256(data).hexdigest()
        if digest != package.sha256:
            raise InstallError(f"{package.name}: downloaded sha256 {digest} does not match the pin")
        extract(data, os.path.join(root, package.dest))
        state[package.name] = package.sha256
        write_state(root, state)
        installed.append(package.name)
    write_pre_hook(root)
    return installed


def write_pre_hook(root):
    """pre.sh is sourced by the image's entry script after every game update, which is when gameinfo.gi gets overwritten."""
    path = os.path.join(root, "pre.sh")
    with open(path, "w", encoding="utf8") as handle:
        handle.write("#!/bin/bash\n" + PRE_HOOK)
    os.chmod(path, 0o755)


def patched_text(text):
    """gameinfo.gi with the Metamod search path added before the csgo one; unchanged when already there."""
    if "csgo/addons/metamod" in text:
        return text
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if line.split("//")[0].split() == ["Game", "csgo"]:
            return "".join(lines[:index] + [METAMOD_LINE] + lines[index:])
    raise InstallError("gameinfo.gi has no `Game csgo` search path to put Metamod before")


def patch_gameinfo(root):
    path = os.path.join(root, "game/csgo/gameinfo.gi")
    with open(path, encoding="utf8") as handle:
        text = handle.read()
    patched = patched_text(text)
    if patched != text:
        with open(path, "w", encoding="utf8") as handle:
            handle.write(patched)
    return patched != text


def main(argv):
    root = os.environ.get("STEAMAPPDIR", "/home/steam/cs2-dedicated")
    command = argv[1] if len(argv) > 1 else ""
    try:
        if command == "ensure":
            print("arena install:", ", ".join(ensure(root)) or "already current")
        elif command == "patch":
            print("arena install: gameinfo.gi", "patched" if patch_gameinfo(root) else "already patched")
        else:
            print("usage: slimm-installer.py ensure|patch", file=sys.stderr)
            return 2
    except (InstallError, OSError) as err:
        print(f"arena install failed: {err}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
