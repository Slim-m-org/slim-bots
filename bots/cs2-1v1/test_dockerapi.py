#!/usr/bin/env python3
"""The real Docker client over a mock transport: label scoping, create body and log framing; run directly: python3 test_dockerapi.py."""

import asyncio
import json
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import httpx  # noqa: E402
from dockerapi import DockerClient, DockerError, ImageMissing, NotOwned, demux_logs, split_image  # noqa: E402
from slimbots.testing import run_module_tests  # noqa: E402

OWN = {"Id": "mine", "Config": {"Labels": {"slimm.bot": "cs2-1v1"}, "Env": []}}
FOREIGN = {"Id": "theirs", "Config": {"Labels": {}, "Env": []}}


def client(handler):
    seen = []

    def wrapped(request):
        seen.append((request.method, request.url.path, dict(request.url.params)))
        return handler(request)

    return DockerClient("http://proxy:2375", transport=httpx.MockTransport(wrapped)), seen


def route(request):
    path = request.url.path
    if path.endswith("/containers/json"):
        return httpx.Response(200, json=[{"Id": "mine", "Labels": {"slimm.bot": "cs2-1v1"}}, {"Id": "theirs", "Labels": {"other": "x"}}])
    if path.endswith("/mine/json"):
        return httpx.Response(200, json=OWN)
    if path.endswith("/theirs/json"):
        return httpx.Response(200, json=FOREIGN)
    if path.endswith("/gone/json"):
        return httpx.Response(404)
    if path.endswith("/containers/create"):
        return httpx.Response(404) if request.url.params["name"] == "nopull" else httpx.Response(201, json={"Id": "new"})
    if path.endswith("/mine/logs"):
        return httpx.Response(200, content=struct.pack(">BxxxI", 1, 5) + b"hello" + struct.pack(">BxxxI", 2, 3) + b"abc")
    return httpx.Response(204)


def test_list_sends_the_label_filter_and_drops_anything_without_the_label():
    docker, seen = client(route)
    found = asyncio.run(docker.list_owned())
    assert [item["Id"] for item in found] == ["mine"]
    assert "slimm.bot=cs2-1v1" in seen[0][2]["filters"]


def test_stop_start_and_logs_refuse_a_foreign_container_without_posting():
    docker, seen = client(route)
    for call in (docker.stop("theirs"), docker.start("theirs"), docker.logs("theirs")):
        try:
            asyncio.run(call)
        except NotOwned:
            continue
        raise AssertionError("a foreign container was accepted")
    assert all(method == "GET" for method, _path, _params in seen)


def test_stop_of_an_owned_container_posts_and_of_a_gone_one_is_a_quiet_success():
    docker, seen = client(route)
    asyncio.run(docker.stop("mine"))
    asyncio.run(docker.stop("gone"))
    assert ("POST", "/containers/mine/stop", {"t": "10"}) in seen
    assert not any("gone/stop" in path for _m, path, _p in seen)


def test_create_adds_the_owner_label_and_a_404_means_the_image_is_missing():
    seen_bodies = []

    def handler(request):
        seen_bodies.append(json.loads(request.content or b"{}"))
        return route(request)

    docker, _ = client(handler)
    assert asyncio.run(docker.create("ok", {"Image": "i", "Labels": {"a": "b"}})) == "new"
    assert seen_bodies[0]["Labels"] == {"a": "b", "slimm.bot": "cs2-1v1"}
    try:
        asyncio.run(docker.create("nopull", {"Image": "i"}))
    except ImageMissing:
        return
    raise AssertionError("a 404 should read as a missing image")


def test_logs_are_demultiplexed_and_pull_splits_a_digest():
    docker, _ = client(route)
    assert asyncio.run(docker.logs("mine")) == "helloabc"
    assert demux_logs(b"") == ""
    assert split_image("a/b@sha256:ff") == ("a/b", "sha256:ff") and split_image("a/b:v1") == ("a/b", "v1") and split_image("a/b") == ("a/b", "latest")


def test_an_unreachable_proxy_and_a_bad_status_become_docker_errors_without_leaking_bodies():
    def refuse(request):
        raise httpx.ConnectError("boom with secret=hunter2")

    docker, _ = client(refuse)
    try:
        asyncio.run(docker.list_owned())
    except DockerError as err:
        assert "hunter2" not in str(err) and "ConnectError" in str(err)
    docker, _ = client(lambda request: httpx.Response(403, text="secret=hunter2"))
    try:
        asyncio.run(docker.list_owned())
    except DockerError as err:
        assert "403" in str(err) and "hunter2" not in str(err)


if __name__ == "__main__":
    run_module_tests(globals())
