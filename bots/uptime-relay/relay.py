"""Turns Uptime Kuma, Grafana and Healthchecks webhooks into one alert shape, and the tiny HTTP listener they post to."""

import asyncio
import hashlib
import hmac
import json
import sys
import urllib.parse
from dataclasses import dataclass

from slimbots.text import clip, one_line

MAX_BODY = 64 * 1024
MAX_HEADER_LINES = 64
SOURCES = ("kuma", "grafana", "healthchecks", "generic")
INSTANCE_LABELS = ("instance", "host", "hostname", "pod", "job", "service")


@dataclass(frozen=True)
class Alert:
    """`ident` tells apart alerts that share a shown name; `former` is the name an alert was keyed by before `ident` existed."""

    source: str
    service: str
    down: bool
    message: str
    url: str | None = None
    ident: str | None = None
    former: str | None = None

    @property
    def key(self):
        return f"{self.source}:{self.ident or self.service}"

    @property
    def former_key(self):
        return f"{self.source}:{self.former}" if self.former else None


class BadPayload(ValueError):
    """The body could not be read as the source it was posted to."""


def _text(value, limit=300):
    return clip(one_line(value), limit)


def parse(source, payload):
    """Every alert one webhook body describes; Grafana can carry several."""
    if not isinstance(payload, dict):
        raise BadPayload("the body is not a JSON object")
    if source == "kuma":
        monitor = payload.get("monitor") or {}
        beat = payload.get("heartbeat") or {}
        name = monitor.get("name") or payload.get("name")
        if not name or "status" not in beat:
            raise BadPayload("an Uptime Kuma body needs monitor.name and heartbeat.status")
        return [Alert("kuma", _text(name, 80), beat["status"] == 0, _text(beat.get("msg") or payload.get("msg")), monitor.get("url"))]
    if source == "grafana":
        alerts = payload.get("alerts")
        found = [_grafana_alert(item, payload) for item in alerts if isinstance(item, dict)] if isinstance(alerts, list) else []
        if not found:
            raise BadPayload("a Grafana body needs an alerts list")
        return found
    if source in ("healthchecks", "generic"):
        name = payload.get("name") or payload.get("service") or payload.get("check")
        status = str(payload.get("status", "")).lower()
        if not name or status not in ("down", "up"):
            raise BadPayload('this body needs a name and a status of "down" or "up"')
        return [Alert(source, _text(name, 80), status == "down", _text(payload.get("message") or payload.get("msg")), payload.get("url"))]
    raise BadPayload(f"unknown source {source!r}")


def _grafana_alert(item, payload):
    """One Grafana entry, keyed by its fingerprint (or its labels) so each instance of a rule is its own alert."""
    labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
    notes = item.get("annotations") if isinstance(item.get("annotations"), dict) else {}
    name = _text(labels.get("alertname") or payload.get("title") or "grafana alert", 80)
    where = next((str(labels[label]) for label in INSTANCE_LABELS if labels.get(label)), None)
    fingerprint = item.get("fingerprint")
    if not isinstance(fingerprint, str) or not fingerprint:
        fingerprint = hashlib.sha256(json.dumps(labels, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return Alert(
        "grafana", _text(f"{name} on {where}", 120) if where else name, item.get("status", payload.get("status")) == "firing",
        _text(notes.get("summary") or notes.get("description") or payload.get("message")), item.get("generatorURL"),
        ident=f"fp:{fingerprint}", former=name,
    )


def route(target):
    """('kuma', token) from '/hook/kuma?token=...'; None for any other path."""
    parsed = urllib.parse.urlsplit(target)
    parts = parsed.path.strip("/").split("/")
    if len(parts) != 2 or parts[0] != "hook" or parts[1] not in SOURCES:
        return None
    token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
    return parts[1], token


def token_ok(given, expected):
    return bool(expected) and hmac.compare_digest(given.encode(), expected.encode())


async def _read_request(reader):
    """(method, target, headers, body) or raises ValueError with the status to answer."""
    line = (await reader.readline()).decode("latin-1").strip()
    try:
        method, target, _ = line.split(" ", 2)
    except ValueError:
        raise ValueError(400) from None
    headers = {}
    for _ in range(MAX_HEADER_LINES):
        raw = (await reader.readline()).decode("latin-1").strip()
        if not raw:
            break
        name, _, value = raw.partition(":")
        headers[name.strip().lower()] = value.strip()
    else:
        raise ValueError(431)
    try:
        length = int(headers.get("content-length", "0"))
    except ValueError:
        raise ValueError(400) from None
    if length > MAX_BODY:
        raise ValueError(413)
    body = await reader.readexactly(length) if length else b""
    return method, target, headers, body


async def _deliver(on_alerts, alerts):
    try:
        delivered = await on_alerts(alerts)
    except Exception as err:
        print(f"{type(err).__name__} handing over {len(alerts)} alert(s): {err}", file=sys.stderr)
        return 503
    return 503 if delivered is False else 204


def _respond(writer, status):
    reasons = {
        204: "No Content", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 405: "Method Not Allowed",
        413: "Payload Too Large", 431: "Request Header Fields Too Large", 503: "Service Unavailable",
    }
    writer.write(f"HTTP/1.1 {status} {reasons.get(status, 'Error')}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n".encode())


async def handle_connection(reader, writer, *, secret, on_alerts, timeout=10):
    """Answers one request: 204 once `on_alerts` delivered them, 503 when it returned False or failed, an error status otherwise."""
    status = 204
    try:
        method, target, headers, body = await asyncio.wait_for(_read_request(reader), timeout)
        matched = route(target)
        if matched is None:
            status = 404
        elif method != "POST":
            status = 405
        else:
            source, query_token = matched
            given = headers.get("x-relay-token") or query_token
            if not token_ok(given, secret):
                status = 401
            else:
                try:
                    alerts = parse(source, json.loads(body or b"null"))
                except (BadPayload, ValueError):
                    status = 400
                else:
                    status = await _deliver(on_alerts, alerts)
    except ValueError as err:
        status = err.args[0] if err.args and isinstance(err.args[0], int) else 400
    except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionError):
        status = 400
    try:
        _respond(writer, status)
        await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()
    return status
