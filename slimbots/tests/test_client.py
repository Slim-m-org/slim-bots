import pytest

from slimbots.client import base_url_problem, socket_url


def test_https_becomes_wss():
    assert socket_url("https://my.space") == "wss://my.space/ws"


@pytest.mark.parametrize(
    "authority", ["localhost:8080", "127.0.0.1:8080", "[::1]:8080"]
)
def test_http_loopback_allowed(authority, capsys):
    url = socket_url(f"http://{authority}")
    assert url == f"ws://{authority}/ws"
    assert "loopback only" in capsys.readouterr().err


def test_http_off_loopback_refused():
    with pytest.raises(RuntimeError, match="https"):
        socket_url("http://example.com")


def test_unknown_scheme_refused():
    with pytest.raises(RuntimeError):
        socket_url("ftp://example.com")


@pytest.mark.parametrize("base", ["https://my.space", "http://localhost:8080", "http://127.0.0.1", "http://[::1]:8080"])
def test_a_base_url_a_token_may_travel_to_has_no_problem(base):
    assert base_url_problem(base) is None


@pytest.mark.parametrize("base", ["http://10.0.0.5:8080", "http://example.com", "ftp://example.com", "example.com"])
def test_a_base_url_that_would_leak_the_token_has_a_problem(base):
    assert "refusing to send a bot token" in base_url_problem(base)
