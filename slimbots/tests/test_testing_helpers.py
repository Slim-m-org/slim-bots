import io
from contextlib import redirect_stdout

from slimbots.bot import Bot
from slimbots.testing import FakeAsyncClient, handle_frame, human, run_module_tests, wire_bot


def test_human_defaults_the_name_from_the_id_and_takes_extras():
    assert human("u1") == {
        "id": "u1", "username": "u1", "display_name": "U1", "is_bot": False, "is_webhook": False,
    }
    assert human("u1", "ann", "Ann", role_ids=["r"])["role_ids"] == ["r"]


def test_wire_bot_gives_the_bot_a_client_space_authors_and_a_store_path():
    bot = Bot(prefix="!")
    client = FakeAsyncClient()
    wire_bot(bot, client, me_id="bot-1", db_name="t.db")
    assert bot.client is client and bot.me_id == "bot-1"
    assert bot.store is None and bot.data_path.endswith("t.db")
    handle_frame(bot, {"type": "ping"})


def test_run_module_tests_runs_every_test_callable_and_reports_the_count():
    ran = []
    namespace = {"test_a": lambda: ran.append("a"), "test_b": lambda: ran.append("b"), "helper": lambda: ran.append("x")}
    out = io.StringIO()
    with redirect_stdout(out):
        run_module_tests(namespace)
    assert ran == ["a", "b"] and "all 2 tests passed" in out.getvalue()
