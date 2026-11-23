#!/usr/bin/env python3
"""The connector manifest's settings are exactly the PELICAN_* names the bot reads, so the Dock's form fills them all,
and every command it declares has a handler in bot.py."""

import json
import os
import pathlib

os.environ.update({"PELICAN_URL": "https://panel.invalid", "PELICAN_API_KEY": "ptlc_x"})

import bot as pelican
import pelican_core as core
from slimbots.testing import run_module_tests

MANIFEST = json.loads((pathlib.Path(__file__).parent / "connector.json").read_text())


class Recorder:
    def __init__(self):
        self.names = []

    def setting(self, name, default=None, **_):
        self.names.append(name)
        return default if name not in os.environ else os.environ[name]


def test_every_setting_the_bot_reads_is_in_the_manifest_but_the_permission_name():
    recorder = Recorder()
    core.configure(recorder)
    declared = {s["key"].upper() for s in MANIFEST["settings"]}
    assert set(recorder.names) - declared == {"PELICAN_CONTROL_PERMISSION"}
    assert declared <= set(recorder.names)


def test_it_is_a_connector_asking_only_to_read_and_post():
    assert MANIFEST["provider"] == "connector"
    assert MANIFEST["addon_api"] >= 7
    assert MANIFEST["connector"]["permissions"] == ["view_channel", "send_messages"]
    secrets = [s["key"] for s in MANIFEST["settings"] if s["type"] == "secret"]
    assert secrets == ["pelican_api_key"]



def test_every_declared_command_has_a_handler_and_the_panel_runs_one():
    points = MANIFEST["extension_points"]
    commands = {p["name"] for p in points if p["kind"] == "command"}
    assert commands == {"servers", "server-panel"}
    assert all(pelican.bot._runs.get(name) for name in commands)
    panels = [p for p in points if p["kind"] == "panel"]
    assert [p["command"] for p in panels] == ["server-panel"]
    assert all(p["permission"] == "view" for p in points)


if __name__ == "__main__":
    run_module_tests(globals())
