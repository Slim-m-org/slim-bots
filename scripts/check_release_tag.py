#!/usr/bin/env python3
"""Fails a PyPI publish whose git tag is not v<version> for the version in slimbots/pyproject.toml."""

from __future__ import annotations

import argparse
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def package_version(root: Path = ROOT) -> str:
    return tomllib.loads((root / "slimbots" / "pyproject.toml").read_text())["project"]["version"]


def tag_problem(tag: str, version: str) -> str | None:
    if tag == f"v{version}":
        return None
    return f"tag {tag!r} is not v{version}, the version in slimbots/pyproject.toml"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("tag", help="the release tag being published, such as v0.19.0")
    args = ap.parse_args(argv)
    problem = tag_problem(args.tag, package_version())
    if problem:
        print(f"FAIL: {problem}")
        return 1
    print(f"{args.tag} matches slimbots/pyproject.toml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
