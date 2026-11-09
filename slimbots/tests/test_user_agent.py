"""A bot that names no user agent identifies as the library version it runs, not a stale constant."""

import importlib.metadata

from slimbots.bot import DEFAULT_USER_AGENT, Bot, library_version


def test_the_version_is_the_installed_package_s(monkeypatch):
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "9.8.7" if name == "slim-m" else "wrong")
    assert library_version() == "9.8.7"


def test_a_checkout_that_was_never_installed_reads_as_dev(monkeypatch):
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", missing)
    assert library_version() == "dev"


def test_the_default_user_agent_carries_that_version():
    assert DEFAULT_USER_AGENT == f"slimbots/{library_version()}"
    assert Bot().user_agent == DEFAULT_USER_AGENT
