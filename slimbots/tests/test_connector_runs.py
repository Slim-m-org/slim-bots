"""A connector answers the runs slim-m forwards for its manifest commands and panels, once each, in the module answer shape."""

import asyncio
import json

import pytest

from slimbots import Bot, Run, RunError, scene
from slimbots.http import ApiError
from slimbots.runs import Run as RunType, answer_of
from slimbots.testing import FakeAsyncClient


def run_frame(command="servers", text="", run_id="r1", deadline_ms=6000, **request):
    return {
        "type": "connector.run", "run_id": run_id, "module_id": "pelican", "deadline_ms": deadline_ms,
        "request": {
            "command": command, "input": text, "caller": {"id": "c" * 64, "name": "Ada"},
            "entropy": "e", "now_ms": 5, "context": {"channel_id": "ch1"}, **request,
        },
    }


def make_bot():
    bot = Bot(prefix="!", connector=True)
    bot.client = FakeAsyncClient()
    return bot


async def deliver(bot, frame):
    await bot._handle_frame(frame)
    await asyncio.gather(*list(bot._background_tasks))


def answers(bot):
    return [(path, body) for method, path, body, _ in bot.client.calls if path.startswith("/connectors/me/runs/")]


async def test_a_run_reaches_its_handler_and_text_is_the_answer():
    bot = make_bot()
    seen = []

    @bot.manifest_command("servers")
    async def servers(run: Run):
        seen.append(run)
        return "two servers up"

    await deliver(bot, run_frame())
    assert answers(bot) == [("/connectors/me/runs/r1", {"ok": True, "output": "two servers up"})]
    run = seen[0]
    assert run.caller.id == "c" * 64 and run.caller.name == "Ada"
    assert run.channel_id == "ch1" and run.input == "" and run.action is None


async def test_a_panel_press_carries_its_action_and_state_and_a_scene_is_sent_as_text():
    bot = make_bot()

    @bot.manifest_command("servers")
    async def servers(run: Run):
        return scene(10, 4, [], state=f"{run.action}:{run.state}")

    await deliver(bot, run_frame(text=json.dumps({"action": "refresh", "state": "page-2"})))
    [(_, body)] = answers(bot)
    assert body["ok"] is True
    painted = json.loads(body["output"])
    assert painted["$slim"] == "scene/1" and painted["state"] == "refresh:page-2"


async def test_a_refusal_an_unknown_command_and_a_crash_each_answer_an_error():
    bot = make_bot()

    @bot.manifest_command("servers")
    async def servers(run: Run):
        raise RunError("the panel is down")

    @bot.manifest_command("boom")
    async def boom(run: Run):
        raise ValueError("bug")

    await deliver(bot, run_frame(run_id="a"))
    await deliver(bot, run_frame(command="nope", run_id="b"))
    await deliver(bot, run_frame(command="boom", run_id="c"))
    assert [body for _, body in answers(bot)] == [
        {"ok": False, "error": "the panel is down"},
        {"ok": False, "error": "this connector has no command 'nope'"},
        {"ok": False, "error": "the connector failed to answer"},
    ]


async def test_a_slow_handler_answers_before_the_hosts_deadline():
    bot = make_bot()

    @bot.manifest_command("servers")
    async def servers(run: Run):
        await asyncio.sleep(5)
        return "too late"

    await deliver(bot, run_frame(deadline_ms=700))
    assert [body for _, body in answers(bot)] == [{"ok": False, "error": "the connector took too long to answer"}]


async def test_an_answer_the_host_stopped_waiting_for_is_dropped_quietly():
    bot = make_bot()
    bot.client.respond("POST", "/connectors/me/runs/r1", ApiError(404, {"error": "no run"}))

    @bot.manifest_command("servers")
    async def servers(run: Run):
        return "x"

    await deliver(bot, run_frame())
    assert len(answers(bot)) == 1


async def test_a_connector_publishes_no_bot_commands_but_a_plain_bot_still_does():
    connector = make_bot()
    await connector._register_commands()
    assert not any(path == "/bots/commands" for _, path, _, _ in connector.client.calls)

    plain = Bot(prefix="!", connector=False)
    plain.client = FakeAsyncClient()
    plain.client.respond("PUT", "/bots/commands", None)
    await plain._register_commands()
    assert any(path == "/bots/commands" for _, path, _, _ in plain.client.calls)


def test_one_command_has_one_handler_and_answers_are_text_or_a_scene():
    bot = make_bot()

    @bot.manifest_command("servers")
    async def servers(run: Run):
        return ""

    with pytest.raises(ValueError):
        bot.manifest_command("servers")(servers)
    assert answer_of(None) == {"ok": True, "output": ""}
    with pytest.raises(TypeError):
        answer_of({"not": "a scene"})


def test_plain_input_is_not_mistaken_for_a_press():
    for text in ["", "hello", "{not json", json.dumps({"state": "x"}), json.dumps([1])]:
        run = RunType.from_request({"command": "servers", "input": text})
        assert run.action is None, text
