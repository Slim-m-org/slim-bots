#!/usr/bin/env python3
"""The call dock's play/pause control follows playback; run directly: python3 test_dock_state.py."""

import asyncio

import dock_state
import session_registry
from test_bot import jellyfin, message, process
from slimbots.testing import run_module_tests
from test_panel import client_in_call, press, started_session, use_control


def armed_client():
    client = client_in_call()
    jellyfin.bot._ui_registered = True
    asyncio.run(jellyfin.bot.update_call_control(dock_state.PLAYPAUSE_ID, icon="pause", label="Pause"))
    client.calls.clear()
    return client


def dock():
    entry = jellyfin.bot._ui.controls[dock_state.PLAYPAUSE_ID][0]
    return entry.icon, entry.label


def last_registered(client):
    puts = [body for method, path, body, _ in client.calls if (method, path) == ("PUT", "/bots/ui")]
    control = next(c for c in puts[-1]["call_controls"] if c["id"] == dock_state.PLAYPAUSE_ID)
    return control["icon"], control["label"]


def test_the_dock_flips_to_play_on_pause_and_back_on_resume_from_the_dock():
    client = armed_client()
    session = started_session(client)
    use_control(client, session, "jf-playpause")
    assert dock() == ("play", "Play") and last_registered(client) == ("play", "Play")
    use_control(client, session, "jf-playpause")
    assert dock() == ("pause", "Pause") and last_registered(client) == ("pause", "Pause")
    session_registry.clear()


def test_the_panel_button_and_the_typed_commands_flip_it_too():
    client = armed_client()
    session = started_session(client)
    press(client, session, "jf:toggle")
    assert dock() == ("play", "Play")
    press(client, session, "jf:toggle")
    assert dock() == ("pause", "Pause")
    process(client, message("!pause"))
    assert dock() == ("play", "Play")
    process(client, message("!resume"))
    assert dock() == ("pause", "Pause")
    session_registry.clear()


def test_stopping_while_paused_puts_the_dock_back_to_pause():
    client = armed_client()
    session = started_session(client)
    use_control(client, session, "jf-playpause")
    assert dock() == ("play", "Play")
    use_control(client, session, "jf-stop")
    assert session.finished and dock() == ("pause", "Pause")
    assert last_registered(client) == ("pause", "Pause")
    session_registry.clear()


def test_a_repeat_with_no_change_sends_nothing():
    client = armed_client()
    session = started_session(client)
    asyncio.run(session.refresh_panel())
    assert not [c for c in client.calls if c[1] == "/bots/ui"]
    session_registry.clear()


def test_with_several_parties_it_shows_play_only_when_all_are_paused():
    class Party:
        def __init__(self, paused):
            self.paused = paused

    assert dock_state.wanted([]) == ("pause", "Pause")
    assert dock_state.wanted([Party(True), Party(False)]) == ("pause", "Pause")
    assert dock_state.wanted([Party(True), Party(True)]) == ("play", "Play")


if __name__ == "__main__":
    run_module_tests(globals())
