#!/usr/bin/env python3
"""The Quick Connect calls against jellyfin's API: the client header, a disabled server, and closing the session a link opens.
Run directly: python3 test_quick_connect.py."""

import io
import json
import os
import urllib.error
import urllib.request

os.environ.setdefault("JELLYFIN_URL", "https://fake-jellyfin.invalid")
os.environ.setdefault("JELLYFIN_API_KEY", "fake-key")

import jellyfin_core  # noqa: E402
import quick_connect  # noqa: E402
from test_resume import Patched  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

PENDING = quick_connect.Pending(code="482913", secret="s3cret", device_id="dev-1", device_name="slim-m link for Nick")


class FakeServer:
    """Answers by `METHOD path`; an exception value is raised, so a test can fail any one call."""

    def __init__(self, answers):
        self.answers, self.requests = answers, []

    def urlopen(self, request, timeout=None):
        path = request.full_url.removeprefix(jellyfin_core.JELLYFIN_URL)
        self.requests.append((request.get_method(), path, request.get_header("Authorization"), request.data))
        answer = self.answers[f"{request.get_method()} {path.split('?')[0]}"]
        if isinstance(answer, BaseException):
            raise answer
        return io.BytesIO(json.dumps(answer).encode() if answer is not None else b"")


def serving(answers):
    server = FakeServer(answers)
    return server, Patched((urllib.request, "urlopen", server.urlopen), (jellyfin_core, "JELLYFIN_URL", "https://jf.test"))


def http_error(code):
    return urllib.error.HTTPError("https://jf.test", code, "error", {}, None)


def test_the_device_name_keeps_only_plain_characters_so_the_header_cannot_be_broken():
    assert quick_connect.device_name('Nick", Token="x') == "slim-m link for Nick Tokenx"
    assert quick_connect.device_name("") == "slim-m link for a member"
    assert len(quick_connect.device_name("n" * 200)) == len("slim-m link for ") + quick_connect.MAX_DEVICE_LABEL


def test_initiate_identifies_the_device_and_returns_the_code():
    server, patch = serving({"POST /QuickConnect/Initiate": {"Code": 482913, "Secret": "s3cret", "Authenticated": False}})
    with patch:
        pending = quick_connect.initiate("Nick")
    assert pending.code == "482913" and pending.secret == "s3cret" and pending.device_name == "slim-m link for Nick"
    header = server.requests[0][2]
    assert header.startswith('MediaBrowser Client="slim-m", Device="slim-m link for Nick", DeviceId="')
    assert "Token=" not in header


def test_initiate_on_a_server_with_quick_connect_off_raises_disabled():
    _, patch = serving({"POST /QuickConnect/Initiate": http_error(401)})
    with patch:
        try:
            quick_connect.initiate("Nick")
        except quick_connect.QuickConnectDisabled:
            return
    raise AssertionError("a 401 from Initiate was not reported as Quick Connect being off")


def test_is_approved_reads_authenticated_and_sends_the_secret():
    server, patch = serving({"GET /QuickConnect/Connect": {"Authenticated": True}})
    with patch:
        assert quick_connect.is_approved(PENDING)
    assert server.requests[0][1] == "/QuickConnect/Connect?secret=s3cret"


def test_redeem_takes_the_identity_from_jellyfin_and_logs_the_session_out():
    server, patch = serving({
        "POST /Users/AuthenticateWithQuickConnect": {"AccessToken": "tok-1", "User": {"Id": "jf-a", "Name": "Alice"}},
        "POST /Sessions/Logout": None,
    })
    with patch:
        approved = quick_connect.redeem(PENDING)
    assert approved == quick_connect.Approved(user_id="jf-a", user_name="Alice")
    assert json.loads(server.requests[0][3]) == {"Secret": "s3cret"}
    method, path, header, _ = server.requests[1]
    assert (method, path) == ("POST", "/Sessions/Logout") and 'Token="tok-1"' in header and 'DeviceId="dev-1"' in header


def test_a_failed_logout_falls_back_to_deleting_the_device_with_the_admin_key():
    server, patch = serving({
        "POST /Users/AuthenticateWithQuickConnect": {"AccessToken": "tok-1", "User": {"Id": "jf-a", "Name": "Alice"}},
        "POST /Sessions/Logout": http_error(500),
        "DELETE /Devices": None,
    })
    with patch:
        quick_connect.redeem(PENDING)
    method, path, header, _ = server.requests[2]
    assert (method, path) == ("DELETE", "/Devices?id=dev-1") and header == jellyfin_core.jellyfin_auth_header()


if __name__ == "__main__":
    run_module_tests(globals())
