import pytest

from slimbots import Bot


def exit_of(bot, **kwargs):
    with pytest.raises(SystemExit) as caught:
        bot.run_cli(**kwargs)
    return caught.value.code


def test_exits_with_the_codes_run_returns(monkeypatch):
    bot = Bot()
    monkeypatch.setattr(bot, "run", lambda: 3)
    assert exit_of(bot) == 3
    monkeypatch.setattr(bot, "run", lambda: None)
    assert exit_of(bot) == 0


def test_a_config_runtime_error_exits_with_its_message(monkeypatch):
    bot = Bot()

    def run():
        raise RuntimeError("set SLIMM_TOKEN")

    monkeypatch.setattr(bot, "run", run)
    assert exit_of(bot) == "set SLIMM_TOKEN"


def test_a_failed_check_exits_before_the_bot_runs(monkeypatch):
    bot = Bot()
    ran = []
    monkeypatch.setattr(bot, "run", lambda: ran.append(1))
    assert exit_of(bot, check=lambda: "JELLYFIN_URL is not set") == "JELLYFIN_URL is not set"
    assert ran == []


def test_checks_run_in_order_and_the_first_failure_wins(monkeypatch):
    bot = Bot()
    monkeypatch.setattr(bot, "run", lambda: 0)
    order = []

    def ok():
        order.append("ok")

    def bad():
        order.append("bad")
        return "bad config"

    def never():
        order.append("never")

    assert exit_of(bot, check=[ok, bad, never]) == "bad config"
    assert order == ["ok", "bad"]


def test_passing_checks_let_the_bot_run(monkeypatch):
    bot = Bot()
    monkeypatch.setattr(bot, "run", lambda: 0)
    assert exit_of(bot, check=[lambda: None, lambda: ""]) == 0
