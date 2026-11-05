#!/usr/bin/env python3
"""Boots a throwaway slim-m server's accounts and smokes every bot that has a manifest, one fresh bot and channel each."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parent.parent
SMOKE_DIR = ROOT / "scripts" / "smoke"
EXEMPT_FILE = SMOKE_DIR / "exempt.json"
PASSWORD = "smoke-" + uuid.uuid4().hex
BOT_DIRS_WITHOUT_BOT = ("_template",)


def bot_names(root: Path = ROOT) -> list[str]:
    """Every directory under bots/ that is a runnable bot."""
    return sorted(p.name for p in (root / "bots").iterdir() if p.is_dir() and p.name not in BOT_DIRS_WITHOUT_BOT and not p.name.startswith("__"))


def manifest_names(smoke_dir: Path = SMOKE_DIR) -> list[str]:
    return sorted(p.stem for p in smoke_dir.glob("*.json") if p.name != EXEMPT_FILE.name)


def unaccounted(bots: list[str], manifests: list[str], exempt: dict[str, str]) -> list[str]:
    """Bots with neither a manifest nor a written reason for having none."""
    return [b for b in bots if b not in manifests and not exempt.get(b)]


class Stack:
    """The account on one fresh deployment that claimed it; it types the commands, so admin-only ones can be smoked too."""

    def __init__(self, base: str) -> None:
        self.http = httpx.Client(base_url=base.rstrip("/"), timeout=30)
        self.admin = self._register("smoke-admin")
        self.admin_headers = {"authorization": f"Bearer {self.admin}"}
        self.tester = self.admin
        self.permissions = self.http.get("/me", headers=self.admin_headers).raise_for_status().json()["permissions"]

    def _register(self, username: str) -> str:
        body: dict[str, Any] = {"username": username, "display_name": username, "password": PASSWORD, "device_name": "smoke"}
        return self.http.post("/auth/register", json=body).raise_for_status().json()["access_token"]

    def bot_and_channel(self, name: str, prefix: str | None = None, kind: str = "text") -> tuple[str, str, str]:
        """A bot holding every permission (bots are full principals) and an unrestricted channel only it and the tester are in."""
        bot = self.http.post("/bots", headers=self.admin_headers, json={"username": f"smoke-{name}"[:32], "permissions": self.permissions, "prefix": prefix})
        channel = self.http.post("/channels", headers=self.admin_headers, json={"name": f"smoke-{name}"[:64], "kind": kind})
        created = bot.raise_for_status().json()
        return created["token"], channel.raise_for_status().json()["id"], created["bot"]["user_id"]


def smoke_one(stack: Stack, base: str, name: str) -> int:
    manifest = json.loads((SMOKE_DIR / f"{name}.json").read_text())
    token, channel, bot_id = stack.bot_and_channel(name, manifest.get("prefix"), manifest.get("channel_kind", "text"))
    env = {
        **os.environ, "SLIMM_URL": base, "SLIMM_BOT_TOKEN": token, "SLIMM_SMOKE_TESTER_TOKEN": stack.tester,
        "SLIMM_SMOKE_CHANNEL": channel, "SLIMM_SMOKE_PRIVATE": "1", "SLIMM_SMOKE_BOT_ID": bot_id,
        "SLIMM_DB_PATH": str(ROOT / "smoke-transcripts" / f"{name}.db"),
    }
    (ROOT / "smoke-transcripts").mkdir(exist_ok=True)
    return subprocess.run([sys.executable, str(ROOT / "scripts" / "smoke_bot.py"), name], env=env, check=False).returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", required=True, help="a freshly started, unclaimed slim-m server; this script claims it")
    ap.add_argument("--only", nargs="*", help="bot names to run instead of every manifest")
    args = ap.parse_args(argv)
    stack = Stack(args.base_url)
    failed = []
    for name in args.only or manifest_names():
        print(f"== {name}", flush=True)
        if smoke_one(stack, args.base_url, name) != 0:
            failed.append(name)
    print(f"failed: {', '.join(failed)}" if failed else "every manifest passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
