"""A bot installed as a slim-m connector reads its settings from the host, before the environment."""

import httpx
import pytest

from slimbots import Bot
from slimbots.connector import ConnectorError, fetch_settings

BASE = "https://space.example"
TOKEN = "slimbot_conn"


def host(settings, *, status=200, seen=None):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        if status != 200:
            return httpx.Response(status, json={"error": "no"})
        return httpx.Response(200, json={"module_id": "pelican", "permissions": 6, "settings": settings})

    return httpx.MockTransport(handle)


def connector_bot(monkeypatch, transport):
    monkeypatch.setenv("SLIMM_URL", BASE)
    monkeypatch.setenv("SLIMM_BOT_TOKEN", TOKEN)
    bot = Bot(connector=True)
    bot._connector_transport = transport
    return bot


def test_the_host_value_wins_over_the_environment_and_is_converted(monkeypatch):
    seen = []
    monkeypatch.setenv("PELICAN_URL", "https://from-env.example")
    bot = connector_bot(monkeypatch, host({
        "pelican_url": "https://panel.example", "pelican_api_key": "ptlc_secret",
        "pelican_status_interval": 90, "pelican_allow_http": True, "pelican_servers": "mc, valheim",
        "pelican_log_channel": None,
    }, seen=seen))
    assert bot.setting("PELICAN_URL") == "https://panel.example"
    assert bot.setting("PELICAN_API_KEY", required=True) == "ptlc_secret"
    assert bot.setting("PELICAN_STATUS_INTERVAL", 60, type=int) == 90
    assert bot.setting("PELICAN_ALLOW_HTTP", False, type=bool) is True
    assert bot.setting("PELICAN_SERVERS", [], type=list) == ["mc", "valheim"]
    assert bot.setting("PELICAN_LOG_CHANNEL", "") == ""
    assert len(seen) == 1, "the settings are read once"
    assert seen[0].url.path == "/connectors/me"
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert bot._setting_errors == []


def test_an_unset_host_value_falls_back_to_the_environment(monkeypatch):
    monkeypatch.setenv("PELICAN_LOG_CHANNEL", "chan-1")
    bot = connector_bot(monkeypatch, host({"pelican_log_channel": None}))
    assert bot.setting("PELICAN_LOG_CHANNEL", "") == "chan-1"


@pytest.mark.parametrize(("status", "reason"), [(403, "not a connector"), (409, "turned this connector off"), (401, "refused")])
def test_a_refusal_is_reported_at_start_with_its_reason(monkeypatch, status, reason):
    monkeypatch.delenv("PELICAN_URL", raising=False)
    bot = connector_bot(monkeypatch, host({}, status=status))
    assert bot.setting("PELICAN_URL", required=True) is None
    joined = " ".join(bot._setting_errors)
    assert reason in joined
    assert TOKEN not in joined


def test_a_value_of_the_wrong_type_is_reported_not_raised(monkeypatch):
    bot = connector_bot(monkeypatch, host({"pelican_status_interval": "soon"}))
    assert bot.setting("PELICAN_STATUS_INTERVAL", 60, type=int) == 60
    assert any("PELICAN_STATUS_INTERVAL" in e for e in bot._setting_errors)


def test_an_ordinary_bot_never_asks_the_host(monkeypatch):
    monkeypatch.delenv("SLIMM_CONNECTOR", raising=False)
    monkeypatch.setenv("PELICAN_URL", "https://from-env.example")

    def refuse(request):
        raise AssertionError("an ordinary bot asked the host")

    bot = Bot()
    bot._connector_transport = httpx.MockTransport(refuse)
    assert bot.setting("PELICAN_URL") == "https://from-env.example"


def test_slimm_connector_turns_it_on(monkeypatch):
    monkeypatch.setenv("SLIMM_CONNECTOR", "1")
    assert Bot().connector is True


def test_the_token_never_travels_in_plaintext_off_loopback():
    with pytest.raises(ConnectorError, match="refusing"):
        fetch_settings("http://space.example", TOKEN, transport=host({}))
