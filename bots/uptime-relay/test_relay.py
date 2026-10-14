#!/usr/bin/env python3
"""Payload parsing per source and the HTTP listener over a real loopback socket; run directly: python3 test_relay.py."""

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import relay  # noqa: E402

KUMA_DOWN = {
    "heartbeat": {"status": 0, "msg": "connect ECONNREFUSED 10.0.0.5:8096", "time": "2026-10-13 18:00:00"},
    "monitor": {"name": "Jellyfin", "url": "http://10.0.0.5:8096"},
    "msg": "[Jellyfin] [Down] connect ECONNREFUSED",
}
KUMA_UP = {**KUMA_DOWN, "heartbeat": {"status": 1, "msg": "200 - OK"}}
GRAFANA = {
    "status": "firing",
    "title": "[FIRING:2] disk",
    "alerts": [
        {"status": "firing", "labels": {"alertname": "DiskFull"}, "annotations": {"summary": "/ is 97% full"}, "generatorURL": "http://grafana/a/1"},
        {"status": "resolved", "labels": {"alertname": "HighLoad"}, "annotations": {"description": "load back under 4"}},
    ],
}


def test_kuma_down_and_up():
    [down] = relay.parse("kuma", KUMA_DOWN)
    assert (down.service, down.down, down.url) == ("Jellyfin", True, "http://10.0.0.5:8096")
    assert "ECONNREFUSED" in down.message
    assert down.key == "kuma:Jellyfin"
    [up] = relay.parse("kuma", KUMA_UP)
    assert not up.down


def test_grafana_carries_one_alert_per_entry():
    alerts = relay.parse("grafana", GRAFANA)
    assert [(a.service, a.down) for a in alerts] == [("DiskFull", True), ("HighLoad", False)]
    assert alerts[0].message == "/ is 97% full"
    assert alerts[1].message == "load back under 4"


def test_healthchecks_and_generic_need_a_name_and_status():
    [alert] = relay.parse("healthchecks", {"name": "nightly backup", "status": "down"})
    assert alert.down and alert.service == "nightly backup"
    for bad in ({"name": "x"}, {"status": "down"}, {"name": "x", "status": "sideways"}, ["not", "an", "object"]):
        try:
            relay.parse("generic", bad)
        except relay.BadPayload:
            continue
        raise AssertionError(f"accepted {bad!r}")


def test_a_long_message_is_trimmed_and_whitespace_collapsed():
    [alert] = relay.parse("generic", {"name": "x", "status": "down", "message": "a  \n b " + "c" * 500})
    assert alert.message.startswith("a b ")
    assert len(alert.message) == 300 and alert.message.endswith("...")


def test_routes_and_tokens():
    assert relay.route("/hook/kuma?token=abc") == ("kuma", "abc")
    assert relay.route("/hook/grafana") == ("grafana", "")
    assert relay.route("/hook/nope") is None
    assert relay.route("/elsewhere") is None
    assert relay.token_ok("abc", "abc")
    assert not relay.token_ok("abd", "abc")
    assert not relay.token_ok("", "")


async def _post(port, path, body, headers=None):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    head = f"POST {path} HTTP/1.1\r\nHost: x\r\nContent-Length: {len(raw)}\r\n"
    for name, value in (headers or {}).items():
        head += f"{name}: {value}\r\n"
    writer.write(head.encode() + b"\r\n" + raw)
    await writer.drain()
    status = int((await reader.readline()).split()[1])
    writer.close()
    return status


def _with_server(scenario):
    received = []

    async def on_alerts(alerts):
        received.extend(alerts)

    async def run():
        async def handler(r, w):
            await relay.handle_connection(r, w, secret="s3cret", on_alerts=on_alerts)

        server = await asyncio.start_server(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return await scenario(port)

    return asyncio.run(run()), received


def test_the_listener_hands_over_alerts_only_with_the_secret():
    async def scenario(port):
        return [
            await _post(port, "/hook/kuma?token=s3cret", KUMA_DOWN),
            await _post(port, "/hook/kuma", KUMA_DOWN, {"X-Relay-Token": "s3cret"}),
            await _post(port, "/hook/kuma?token=wrong", KUMA_DOWN),
            await _post(port, "/hook/kuma", KUMA_DOWN),
        ]

    statuses, received = _with_server(scenario)
    assert statuses == [204, 204, 401, 401]
    assert len(received) == 2


def test_the_listener_refuses_bad_bodies_paths_and_oversize():
    async def scenario(port):
        return [
            await _post(port, "/hook/kuma?token=s3cret", b"{not json"),
            await _post(port, "/hook/kuma?token=s3cret", {"monitor": {}}),
            await _post(port, "/nowhere?token=s3cret", KUMA_DOWN),
            await _post(port, "/hook/kuma?token=s3cret", b"x" * (relay.MAX_BODY + 1)),
        ]

    statuses, received = _with_server(scenario)
    assert statuses == [400, 400, 404, 413]
    assert not received


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"all {len(tests)} tests passed")
