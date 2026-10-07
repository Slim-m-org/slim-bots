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
    client = _client(lambda request: httpx.Response(429, json={"error": "slow down"}))
    delays, sleep = _recorder()
    with pytest.raises(ApiError) as caught:
        await client.call("GET", "/x", retries=4, base_delay=1.0, max_delay=3.0, sleep=sleep)
    assert caught.value.status == 429
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
