"""`JsonService`: JSON in and out over httpx, key rejection, unreachable hosts, redirects, size caps and the destination policy."""

import asyncio
import json
import ssl

import httpx
import pytest

from slimbots import jellyfin
from slimbots.urlpolicy import PRIVATE
from slimbots.web import (
    DEFAULT_PUBLIC_MAX_BYTES, JsonService, ServiceAuthError, ServiceBlocked, ServiceError, ServiceHTTPError, ServiceTooLarge,
    ServiceUnreachable,
)


def serving(handler, **options):
    seen = []

    def record(request):
        seen.append(request)
        return handler(request)

    return JsonService("demo", "https://api.test/v1/", transport=httpx.MockTransport(record), **options), seen


async def test_a_get_sends_the_query_the_headers_and_returns_parsed_json():
    service, seen = serving(lambda r: httpx.Response(200, json={"ok": True}), headers={"X-Api-Key": "k"}, user_agent="demo-bot/1")
    assert await service.get("/items", {"q": "a b", "ids": [1, 2]}) == {"ok": True}
    request = seen[0]
    assert str(request.url) == "https://api.test/v1/items?q=a+b&ids=1&ids=2"
    assert request.headers["x-api-key"] == "k" and request.headers["user-agent"] == "demo-bot/1"


async def test_a_post_sends_a_json_body():
    service, seen = serving(lambda r: httpx.Response(201, json={"id": 3}))
    assert await service.post("/items", {"name": "x"}, {"a": "1"}) == {"id": 3}
    request = seen[0]
    assert request.method == "POST" and json.loads(request.content) == {"name": "x"}
    assert request.headers["content-type"] == "application/json" and request.url.query == b"a=1"


async def test_an_empty_body_is_none_and_a_non_json_body_is_an_error():
    service, _ = serving(lambda r: httpx.Response(204))
    assert await service.get("/x") is None
    broken, _ = serving(lambda r: httpx.Response(200, content=b"<html>"))
    with pytest.raises(ServiceError, match="not JSON"):
        await broken.get("/x")


@pytest.mark.parametrize("status, kind", [(401, ServiceAuthError), (403, ServiceAuthError), (404, ServiceHTTPError), (500, ServiceHTTPError)])
async def test_an_error_status_is_a_typed_error_carrying_the_status(status, kind):
    service, _ = serving(lambda r: httpx.Response(status))
    with pytest.raises(kind) as caught:
        await service.get("/x")
    assert caught.value.status == status


async def test_which_statuses_mean_a_rejected_key_is_the_callers_choice():
    service, _ = serving(lambda r: httpx.Response(403), auth_statuses=(401,))
    with pytest.raises(ServiceHTTPError):
        await service.get("/x")


@pytest.mark.parametrize("failure", [httpx.ConnectError("refused"), httpx.ReadTimeout("slow"), httpx.RemoteProtocolError("reset")])
async def test_a_transport_failure_is_unreachable_and_an_oserror(failure):
    def boom(request):
        raise failure

    service, _ = serving(boom)
    with pytest.raises(ServiceUnreachable) as caught:
        await service.get("/x?token=secret")
    assert isinstance(caught.value, OSError) and "secret" not in str(caught.value) and type(failure).__name__ in str(caught.value)


async def test_an_error_message_never_carries_the_key_or_the_query():
    service, _ = serving(lambda r: httpx.Response(401), headers={"authorization": "Bearer hunter2"})
    with pytest.raises(ServiceAuthError) as caught:
        await service.get("/x", {"api_key": "hunter2"})
    assert "hunter2" not in str(caught.value)


async def test_get_bytes_returns_the_body_and_raises_on_an_error_status():
    service, _ = serving(lambda r: httpx.Response(200, content=b"\x89PNG"))
    assert await service.get_bytes("/img") == b"\x89PNG"
    missing, _ = serving(lambda r: httpx.Response(404))
    with pytest.raises(ServiceHTTPError):
        await missing.get_bytes("/img")


async def test_send_returns_any_status_as_a_result_for_a_conditional_get():
    service, seen = serving(lambda r: httpx.Response(304, headers={"ETag": "E"}))
    response = await service.send("GET", "/feed", headers={"If-None-Match": "E"})
    assert (response.status, response.headers["etag"]) == (304, "E") and seen[0].headers["if-none-match"] == "E"


async def test_a_body_over_the_cap_is_refused():
    service, _ = serving(lambda r: httpx.Response(200, content=b"x" * 101), max_bytes=100)
    with pytest.raises(ServiceTooLarge):
        await service.send("GET", "/big")
    fits, _ = serving(lambda r: httpx.Response(200, content=b"x" * 100), max_bytes=100)
    assert len((await fits.send("GET", "/ok")).body) == 100


def redirecting(target, status=302):
    def handler(request):
        if request.url.path == "/v1/start":
            return httpx.Response(status, headers={"location": target})
        return httpx.Response(200, json={"at": str(request.url), "auth": request.headers.get("x-api-key")})

    return handler


async def test_a_same_origin_redirect_is_followed_with_the_headers():
    service, seen = serving(redirecting("/v1/end"), headers={"x-api-key": "k"})
    result = await service.get("/start")
    assert result == {"at": "https://api.test/v1/end", "auth": "k"} and len(seen) == 2


async def test_a_redirect_to_another_host_is_refused_so_a_key_is_never_replayed_there():
    service, seen = serving(redirecting("https://evil.test/steal"), headers={"x-api-key": "k"})
    with pytest.raises(ServiceError, match="another host"):
        await service.get("/start")
    assert len(seen) == 1


async def test_a_redirect_to_another_host_is_followed_when_the_service_holds_no_key():
    service, _ = serving(redirecting("https://other.test/v1/end"), cross_origin_redirects=True)
    assert (await service.get("/start"))["at"] == "https://other.test/v1/end"


async def test_a_service_that_follows_no_redirect_reports_the_status():
    service, _ = serving(redirecting("/v1/end"), max_redirects=0)
    with pytest.raises(ServiceHTTPError) as caught:
        await service.get("/start")
    assert caught.value.status == 302


async def test_a_303_turns_a_post_into_a_get():
    methods = []

    def handler(request):
        methods.append(request.method)
        return httpx.Response(303, headers={"location": "/v1/end"}) if request.url.path == "/v1/start" else httpx.Response(200, json={})

    service, _ = serving(handler)
    await service.post("/start", {"a": 1})
    assert methods == ["POST", "GET"]


async def test_a_redirect_loop_stops_at_the_limit():
    service, seen = serving(lambda r: httpx.Response(302, headers={"location": "/v1/start"}), max_redirects=3)
    with pytest.raises(ServiceHTTPError):
        await service.get("/start")
    assert len(seen) == 4


def resolving(*addresses):
    async def resolve(host):
        return list(addresses)

    return resolve


async def test_public_only_refuses_a_private_destination_before_any_request():
    service = JsonService(
        "feed", transport=httpx.MockTransport(lambda r: httpx.Response(200)), public_only=True, resolver=resolving("10.0.0.5"),
    )
    with pytest.raises(ServiceBlocked) as caught:
        await service.send("GET", "https://lan.example/feed")
    assert caught.value.reason == PRIVATE


async def test_public_only_checks_every_redirect_hop():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"}) if request.headers["host"] == "feed.example" else httpx.Response(200)

    service = JsonService(
        "feed", transport=httpx.MockTransport(handler), public_only=True, cross_origin_redirects=True,
        resolver=resolving("93.184.216.34"),
    )
    with pytest.raises(ServiceBlocked):
        await service.send("GET", "https://feed.example/a")


async def test_public_only_lets_the_operator_opt_in_to_a_private_destination():
    service = JsonService(
        "feed", transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"ok")), public_only=True,
        allow_private=True, resolver=resolving("10.0.0.5"),
    )
    assert (await service.send("GET", "http://lan.example/feed")).body == b"ok"


def vetted(handler, resolver, **options):
    seen = []

    def record(request):
        seen.append(request)
        return handler(request)

    service = JsonService("feed", transport=httpx.MockTransport(record), public_only=True, resolver=resolver, **options)
    return service, seen


async def test_public_only_connects_to_the_address_it_vetted_and_keeps_the_host_and_server_name():
    service, seen = vetted(lambda r: httpx.Response(200, content=b"ok"), resolving("93.184.216.34"))
    await service.send("GET", "https://feed.example:8443/a?x=1")
    request = seen[0]
    assert (request.url.host, request.url.port, request.url.query) == ("93.184.216.34", 8443, b"x=1")
    assert request.headers["host"] == "feed.example:8443" and request.extensions["sni_hostname"] == "feed.example"


async def test_a_name_that_answers_differently_on_the_second_lookup_cannot_reach_the_lan():
    answers = [["93.184.216.34"], ["10.0.0.5"]]
    lookups = []

    async def rebinding(host):
        lookups.append(host)
        return answers[min(len(lookups) - 1, 1)]

    service, seen = vetted(lambda r: httpx.Response(200), rebinding)
    await service.send("GET", "http://feed.example/a")
    assert len(lookups) == 1 and [r.url.host for r in seen] == ["93.184.216.34"]


async def test_a_plain_http_destination_sets_no_server_name_and_a_v6_answer_is_a_valid_host():
    service, seen = vetted(lambda r: httpx.Response(200), resolving("2606:4700:4700::1111"))
    await service.send("GET", "http://feed.example/a")
    assert seen[0].url.host == "2606:4700:4700::1111" and seen[0].headers["host"] == "feed.example"
    assert "sni_hostname" not in seen[0].extensions


async def test_a_literal_address_and_an_operator_opt_in_are_not_rewritten():
    service, seen = vetted(lambda r: httpx.Response(200), resolving("93.184.216.34"))
    await service.send("GET", "http://93.184.216.34/a")
    lan = JsonService(
        "lan", transport=httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200)), public_only=True,
        allow_hosts=("lan.example",), resolver=resolving("10.0.0.5"),
    )
    await lan.send("GET", "http://lan.example/a")
    assert [(r.url.host, r.headers["host"]) for r in seen] == [("93.184.216.34", "93.184.216.34"), ("lan.example", "lan.example")]


async def test_a_dead_address_falls_back_to_the_next_one_the_lookup_vouched_for():
    def handler(request):
        if request.url.host == "2606:4700:4700::1111":
            raise httpx.ConnectError("network unreachable")
        return httpx.Response(200, content=b"ok")

    service, seen = vetted(handler, resolving("2606:4700:4700::1111", "93.184.216.34"))
    assert (await service.send("GET", "https://feed.example/a")).body == b"ok"
    assert [r.url.host for r in seen] == ["2606:4700:4700::1111", "93.184.216.34"]


async def test_every_address_failing_is_unreachable():
    def handler(request):
        raise httpx.ConnectError("refused")

    service, seen = vetted(handler, resolving("93.184.216.34", "93.184.216.35"))
    with pytest.raises(ServiceUnreachable):
        await service.send("GET", "https://feed.example/a")
    assert len(seen) == 2


async def test_each_redirect_hop_is_looked_up_and_pinned_on_its_own():
    hosts = {"first.example": "93.184.216.34", "second.example": "93.184.216.35"}

    async def resolve(host):
        return [hosts[host]]

    def handler(request):
        if request.headers["host"] == "first.example":
            return httpx.Response(302, headers={"location": "https://second.example/b"})
        return httpx.Response(200, content=b"done")

    service, seen = vetted(handler, resolve, cross_origin_redirects=True)
    response = await service.send("GET", "https://first.example/a")
    assert [r.url.host for r in seen] == ["93.184.216.34", "93.184.216.35"] and response.url == "https://second.example/b"


async def test_a_slow_drip_body_is_cut_off_by_the_overall_deadline():
    async def drip():
        for _ in range(100):
            await asyncio.sleep(0.02)
            yield b"x"

    service = JsonService(
        "feed", transport=httpx.MockTransport(lambda r: httpx.Response(200, content=drip())), timeout=5, total_timeout=0.1,
    )
    with pytest.raises(ServiceUnreachable, match="did not finish"):
        await service.send("GET", "https://api.test/slow")


async def test_the_overall_deadline_spans_every_redirect_hop():
    async def slow_bounce(request):
        await asyncio.sleep(0.05)
        return httpx.Response(302, headers={"location": "/again"})

    service = JsonService(
        "feed", "https://api.test", transport=httpx.MockTransport(slow_bounce), max_redirects=10, total_timeout=0.12,
    )
    with pytest.raises(ServiceUnreachable, match="did not finish"):
        await service.send("GET", "/again")


async def test_a_service_can_opt_out_of_the_overall_deadline():
    async def slow(request):
        await asyncio.sleep(0.05)
        return httpx.Response(200, content=b"ok")

    service = JsonService("feed", "https://api.test", transport=httpx.MockTransport(slow), total_timeout=None)
    assert (await service.send("GET", "/x")).body == b"ok"


async def test_a_public_only_service_caps_the_body_unless_told_otherwise():
    assert JsonService("a", public_only=True).max_bytes == DEFAULT_PUBLIC_MAX_BYTES
    assert JsonService("a", public_only=True, max_bytes=5).max_bytes == 5
    assert JsonService("a").max_bytes is None
    service, _ = vetted(lambda r: httpx.Response(200, content=b"x" * (DEFAULT_PUBLIC_MAX_BYTES + 1)), resolving("93.184.216.34"))
    with pytest.raises(ServiceTooLarge):
        await service.send("GET", "https://feed.example/big")


async def test_peek_reads_only_the_head_and_reports_where_the_redirects_ended():
    async def endless():
        while True:
            await asyncio.sleep(0)
            yield b"a" * 400

    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://cdn.test/live"})
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=endless())

    service = JsonService("stream", transport=httpx.MockTransport(handler), cross_origin_redirects=True, total_timeout=2)
    response = await service.peek("https://radio.test/start", size=512)
    assert (response.status, response.url, len(response.body)) == (200, "https://cdn.test/live", 512)
    assert response.headers["content-type"] == "audio/mpeg"


async def test_peek_checks_every_hop_of_the_destination_policy():
    def handler(request):
        return httpx.Response(302, headers={"location": "http://10.0.0.5/admin"}) if request.headers["host"] == "radio.example" else httpx.Response(200)

    service, seen = vetted(handler, resolving("93.184.216.34"), cross_origin_redirects=True)
    with pytest.raises(ServiceBlocked):
        await service.peek("https://radio.example/live")
    assert len(seen) == 1


def test_a_custom_ca_context_reaches_the_client():
    context = ssl.create_default_context()
    assert JsonService("pve", ssl_context=context)._verify is context
    assert JsonService("pve")._verify is True


def test_a_service_with_a_base_url_takes_only_paths_and_one_without_takes_only_urls():
    based = JsonService("a", "https://api.test")
    assert based.url_for("/x") == "https://api.test/x"
    with pytest.raises(ValueError):
        based.url_for("https://evil.test/x")
    bare = JsonService("b")
    assert bare.url_for("https://any.test/x") == "https://any.test/x"
    with pytest.raises(ValueError):
        bare.url_for("/x")


async def test_the_jellyfin_service_sends_its_token_and_reads_only_401_as_a_rejected_key():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(int(request.url.params["status"]), json={})

    service = jellyfin.service("https://jf.test", "key", user_agent="bot/1", transport=httpx.MockTransport(handler))
    await service.get("/Items", {"status": 200})
    assert seen[0].headers["authorization"] == 'MediaBrowser Token="key"' and seen[0].headers["user-agent"] == "bot/1"
    with pytest.raises(jellyfin.JellyfinAuthError):
        await service.get("/Items", {"status": 401})
    with pytest.raises(ServiceHTTPError):
        await service.get("/Items", {"status": 403})


def test_the_jellyfin_url_rule_is_the_shared_policy():
    assert jellyfin.url_problem("") is None
    assert jellyfin.url_problem("https://jf.example") is None
    assert jellyfin.url_problem("http://localhost:8096") is None
    assert "plaintext" in jellyfin.url_problem("http://jf.example")
    assert "plaintext" in jellyfin.url_problem("http://jellyfin:8096")
