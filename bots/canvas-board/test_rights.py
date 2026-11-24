#!/usr/bin/env python3
"""The invoker's own canvas rights in CANVAS_CHANNEL gate every board edit; run directly: python3 test_rights.py."""

import asyncio

import bot as board
from slimbots.http import ApiError
from slimbots.permissions import Permissions
from slimbots.testing import run_module_tests
from test_bot import frame, grant_on_canvas, message, process, setup

MEMBER = Permissions.VIEW_CHANNEL | Permissions.USE_CANVAS
SPECTATOR = Permissions.VIEW_CHANNEL | Permissions.CONNECT


def seed(*added_by):
    for slot, author in enumerate(added_by):
        board.bot.store.connection.execute(
            "INSERT INTO items (id, slot, text, seq, active, added_by) VALUES (?, ?, ?, ?, 1, ?)",
            (f"o{slot}", slot, f"note {slot}", slot + 1, author),
        )
    board.bot.store.connection.commit()


def canvas_writes(client):
    return [(m, p) for m, p, *_ in client.calls if m in ("POST", "PATCH") and "/canvas/" in p]


def test_a_canvas_spectator_cannot_add_or_move_notes():
    client = setup()
    seed("u1")
    grant_on_canvas(client, SPECTATOR)
    process(client, message("!board add buy milk", "m1"), message("!board move 1 2", "m2"))
    assert all("Use the canvas" in m["content"] for m in client.sent[-2:]), client.sent[-2:]
    assert not canvas_writes(client)
    assert board.active_items(board.bot.store.connection)[0][1] == 0


def test_a_canvas_spectator_cannot_mark_even_their_own_note_done():
    client = setup()
    seed("u1")
    grant_on_canvas(client, SPECTATOR)
    process(client, message("!board done 1"))
    assert "Use the canvas" in client.sent[-1]["content"]
    assert not canvas_writes(client)


def test_a_member_without_manage_canvas_cannot_remove_another_members_note():
    client = setup()
    seed("u-other", None)
    grant_on_canvas(client, MEMBER)
    process(client, message("!board done 1", "m1"), message("!board done 2", "m2"))
    assert all("Manage Canvas" in m["content"] for m in client.sent[-2:]), client.sent[-2:]
    assert len(board.active_items(board.bot.store.connection)) == 2 and not canvas_writes(client)


def test_a_member_marks_their_own_note_done():
    client = setup()
    seed("u1")
    grant_on_canvas(client, MEMBER)
    client.respond("POST", "/channels/c1/canvas/ops", None)
    process(client, message("!board done 1"))
    assert client.sent[-1]["content"] == "done: note 0"


def test_manage_canvas_removes_another_members_note():
    client = setup()
    seed("u-other")
    grant_on_canvas(client, MEMBER | Permissions.MANAGE_CANVAS)
    client.respond("POST", "/channels/c1/canvas/ops", None)
    process(client, message("!board done 1"))
    assert client.sent[-1]["content"] == "done: note 0"


def test_a_member_without_manage_canvas_cannot_clear_a_board_with_others_notes():
    client = setup()
    seed("u1", "u-other")
    grant_on_canvas(client, MEMBER)
    process(client, message("!board clear"))
    assert "Manage Canvas" in client.sent[-1]["content"]
    assert len(board.active_items(board.bot.store.connection)) == 2 and not canvas_writes(client)


def test_a_member_clears_a_board_of_only_their_own_notes():
    client = setup()
    seed("u1", "u1")
    grant_on_canvas(client, MEMBER)
    client.respond("POST", "/channels/c1/canvas/ops", None)

    async def run():
        await board.bot._handle_frame(frame("!board clear", "m1"))
        await asyncio.sleep(0.01)
        await board.bot._handle_frame(frame("yes", "m2"))
        await asyncio.sleep(0.01)

    asyncio.run(run())
    assert "cleared 2 item(s)" in client.sent[-1]["content"]


def test_unreadable_rights_refuse_an_edit():
    client = setup()
    client.respond("GET", "/members/u1/permissions?channel_id=c1", ApiError(500, {"error": "boom"}))
    process(client, message("!board add buy milk"))
    assert not canvas_writes(client) and board.active_items(board.bot.store.connection) == []


if __name__ == "__main__":
    run_module_tests(globals())
