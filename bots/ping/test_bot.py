#!/usr/bin/env python3
"""Where ping's token may go, with the standard library only like the bot itself; run directly: python3 test_bot.py."""

import asyncio
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bot as ping  # noqa: E402


class _Stop(BaseException):
    """Raised by the fake opener so a bot that would retry forever stops at its first request."""


def _serve(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_a_plaintext_base_is_refused_before_any_request():
    sent = []

    def record(self, request, *args, **kwargs):
        sent.append(request.full_url if hasattr(request, "full_url") else request)
        raise _Stop

    saved = urllib.request.OpenerDirector.open, ping.BASE, ping.TOKEN
    urllib.request.OpenerDirector.open = record
    ping.BASE, ping.TOKEN = "http://slim.example.com", "slimbot_secret"
    try:
        assert asyncio.run(ping.main()) == 2
    except _Stop:
        pass
    finally:
        urllib.request.OpenerDirector.open, ping.BASE, ping.TOKEN = saved
    assert sent == [], sent


def test_the_token_never_follows_a_redirect():
    seen = []

    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            seen.append(self.headers.get("authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"id": "stolen"}')

        def log_message(self, *args):
            pass

    target, target_url = _serve(Target)

    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("location", f"{target_url}/steal")
            self.end_headers()

        def log_message(self, *args):
            pass

    origin, origin_url = _serve(Redirect)
    saved = ping.BASE, ping.TOKEN
    ping.BASE, ping.TOKEN = origin_url, "slimbot_secret"
    try:
        ping.call("GET", "/me")
        raise AssertionError("a redirect should not be followed")
    except urllib.error.HTTPError as err:
        assert err.code == 302
    finally:
        ping.BASE, ping.TOKEN = saved
        origin.shutdown()
        target.shutdown()
    assert all(header is None for header in seen), seen


def test_only_https_or_loopback_http_may_carry_the_token():
    for base in ("https://slim.example.com", "http://localhost:8080", "http://127.0.0.1:8080", "http://[::1]:8080"):
        assert ping.base_problem(base) is None, base
    for base in ("http://slim.example.com", "http://10.0.0.5", "ws://127.0.0.1", "slim.example.com"):
        assert "refusing to send a bot token" in ping.base_problem(base), base


if __name__ == "__main__":
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        fn()
        print(f"PASS: {name}")
    print(f"all {len(tests)} tests passed")
