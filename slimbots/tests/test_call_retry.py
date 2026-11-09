import httpx
import pytest

from slimbots.http import ApiError, AsyncClient


def _client(handler):
    client = AsyncClient("https://fake.invalid", "slimbot_fake", "test/1.0")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://fake.invalid")
    return client


def _recorder():
    delays = []

    async def sleep(seconds):
        delays.append(seconds)

    return delays, sleep


async def test_a_503_is_retried_with_doubling_backoff_until_it_succeeds():
    replies = iter([503, 503, 200])
    client = _client(lambda request: httpx.Response(next(replies), json={"ok": True}))
    delays, sleep = _recorder()
    assert await client.call("GET", "/x", sleep=sleep) == {"ok": True}
    assert delays == [0.5, 1.0]


async def test_backoff_is_capped_and_the_last_error_is_raised_after_the_retries():
    client = _client(lambda request: httpx.Response(503, json={"error": "busy"}))
    delays, sleep = _recorder()
    with pytest.raises(ApiError) as caught:
        await client.call("GET", "/x", retries=4, base_delay=1.0, max_delay=3.0, sleep=sleep)
    assert caught.value.status == 503
    assert delays == [1.0, 2.0, 3.0, 3.0]


@pytest.mark.parametrize("status", [400, 401, 403, 404, 409])
async def test_a_certain_4xx_is_never_retried(status):
    client = _client(lambda request: httpx.Response(status, json={"error": "no"}))
    delays, sleep = _recorder()
    with pytest.raises(ApiError) as caught:
        await client.call("GET", "/x", sleep=sleep)
    assert caught.value.status == status
    assert delays == []


async def test_a_network_failure_is_retried_then_raised_with_no_status():
    def handler(request):
        raise httpx.ConnectError("boom")

    client = _client(handler)
    delays, sleep = _recorder()
    with pytest.raises(ApiError) as caught:
        await client.call("GET", "/x", retries=2, sleep=sleep)
    assert caught.value.status is None
    assert delays == [0.5, 1.0]


async def test_a_429_is_retried_after_the_wait_its_retry_after_header_names():
    replies = iter([httpx.Response(429, headers={"retry-after": "3"}, json={"error": "slow down"}), httpx.Response(200, json={"ok": True})])
    client = _client(lambda request: next(replies))
    delays, sleep = _recorder()
    assert await client.call("GET", "/x", sleep=sleep) == {"ok": True}
    assert delays == [3.0]


async def test_a_429_without_the_header_uses_the_retry_after_seconds_in_the_body():
    replies = iter([httpx.Response(429, json={"error": "slow mode: wait 2s", "retry_after_seconds": 2}), httpx.Response(200, json={})])
    client = _client(lambda request: next(replies))
    delays, sleep = _recorder()
    await client.call("POST", "/x", {"a": 1}, sleep=sleep)
    assert delays == [2.0]


async def test_a_429_asking_for_a_longer_wait_than_the_cap_is_raised_without_retrying():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(429, headers={"retry-after": "30"}, json={"error": "slow mode: wait 30s", "retry_after_seconds": 30})

    client = _client(handler)
    delays, sleep = _recorder()
    with pytest.raises(ApiError) as caught:
        await client.call("POST", "/x", sleep=sleep)
    assert caught.value.status == 429 and caught.value.retry_after == 30.0
    assert len(seen) == 1 and delays == []


async def test_a_429_that_names_no_wait_is_a_quota_and_is_raised_at_once():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(429, json={"error": "slow down and retry"})

    client = _client(handler)
    delays, sleep = _recorder()
    with pytest.raises(ApiError) as caught:
        await client.call("POST", "/x", sleep=sleep)
    assert caught.value.retry_after is None
    assert len(seen) == 1 and delays == []


@pytest.mark.parametrize("asked", ["soon", "-1", "nan", "inf", ""])
async def test_an_unusable_retry_after_is_ignored(asked):
    client = _client(lambda request: httpx.Response(429, headers={"retry-after": asked}, json={"error": "slow down"}))
    delays, sleep = _recorder()
    with pytest.raises(ApiError) as caught:
        await client.call("GET", "/x", sleep=sleep)
    assert caught.value.retry_after is None and delays == []


async def test_the_wait_cap_is_the_callers_to_set():
    replies = iter([httpx.Response(429, headers={"retry-after": "30"}), httpx.Response(200, json={})])
    client = _client(lambda request: next(replies))
    delays, sleep = _recorder()
    await client.call("GET", "/x", max_retry_after=60.0, sleep=sleep)
    assert delays == [30.0]


@pytest.mark.parametrize("send", ["reply", "press"])
async def test_a_private_reply_is_never_retried_because_a_repeat_would_be_a_second_reply(send):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(502, text="bad gateway")

    client = _client(handler)
    with pytest.raises(ApiError) as caught:
        if send == "reply":
            await client.send_ephemeral("c1", "m1", "hi")
        else:
            await client.send_ephemeral_to_press("c1", "i1", "hi")
    assert caught.value.status == 502
    assert len(seen) == 1


@pytest.mark.parametrize("send", ["reply", "press"])
async def test_a_private_reply_retries_a_429_that_names_a_wait(send):
    replies = iter([httpx.Response(429, headers={"retry-after": "0"}, json={"error": "slow down"}), httpx.Response(200, json={"id": "e1"})])
    seen = []

    def handler(request):
        seen.append(request)
        return next(replies)

    client = _client(handler)
    if send == "reply":
        assert await client.send_ephemeral("c1", "m1", "hi") == {"id": "e1"}
    else:
        assert await client.send_ephemeral_to_press("c1", "i1", "hi") == {"id": "e1"}
    assert len(seen) == 2


async def test_a_private_reply_is_not_retried_after_a_network_failure():
    seen = []

    def handler(request):
        seen.append(request)
        raise httpx.ReadTimeout("slow")

    client = _client(handler)
    with pytest.raises(ApiError) as caught:
        await client.send_ephemeral("c1", "m1", "hi")
    assert caught.value.status is None
    assert len(seen) == 1


async def test_only_unprocessed_retries_a_named_429_and_nothing_else():
    replies = iter([httpx.Response(429, headers={"retry-after": "3"}), httpx.Response(200, json={"ok": True})])
    client = _client(lambda request: next(replies))
    delays, sleep = _recorder()
    assert await client.call("POST", "/x", {"a": 1}, only_unprocessed=True, sleep=sleep) == {"ok": True}
    assert delays == [3.0]

    seen = []

    def server_error(request):
        seen.append(request)
        return httpx.Response(503, json={"error": "busy"})

    delays, sleep = _recorder()
    with pytest.raises(ApiError):
        await _client(server_error).call("POST", "/x", only_unprocessed=True, sleep=sleep)
    assert len(seen) == 1 and delays == []


async def test_only_unprocessed_still_raises_a_429_that_names_no_wait():
    client = _client(lambda request: httpx.Response(429, json={"error": "three private replies per message"}))
    delays, sleep = _recorder()
    with pytest.raises(ApiError) as caught:
        await client.call("POST", "/x", only_unprocessed=True, sleep=sleep)
    assert caught.value.status == 429 and delays == []
