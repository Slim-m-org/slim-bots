import httpx

from slimbots.http import AsyncClient


async def test_raw_body_sends_bytes_as_is_not_json():
    seen = {}

    def handler(request):
        seen["content"] = request.content
        seen["json_header"] = request.headers.get("content-type")
        return httpx.Response(200, json={"id": "att-1"})

    client = AsyncClient("https://fake.invalid", "slimbot_fake", "test/1.0")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://fake.invalid")

    result = await client.call("POST", "/attachments", raw_body=b"\x89PNGdata")
    assert seen["content"] == b"\x89PNGdata"
    assert result == {"id": "att-1"}


async def test_json_body_still_works_when_raw_body_is_not_given():
    seen = {}

    def handler(request):
        seen["content"] = request.content
        return httpx.Response(200, json={"ok": True})

    client = AsyncClient("https://fake.invalid", "slimbot_fake", "test/1.0")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://fake.invalid")

    await client.call("POST", "/x", {"a": 1})
    assert seen["content"] == b'{"a":1}' or b"a" in seen["content"]


def _client(handler):
    client = AsyncClient("https://fake.invalid", "slimbot_fake", "test/1.0")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://fake.invalid", headers={"authorization": "Bearer slimbot_fake"})
    return client


async def test_download_returns_the_raw_body_with_the_bots_auth():
    seen = {}

    def handler(request):
        seen["auth"], seen["url"] = request.headers.get("authorization"), str(request.url)
        return httpx.Response(200, content=b"\x89PNGbytes")

    assert await _client(handler).download("/attachments/abc") == b"\x89PNGbytes"
    assert seen == {"auth": "Bearer slimbot_fake", "url": "https://fake.invalid/attachments/abc"}


async def test_download_refuses_a_body_past_max_bytes_even_without_a_content_length():
    from slimbots.http import ApiError, ResponseTooLarge

    async def stream():
        yield b"a" * 60
        yield b"b" * 60

    def handler(request):
        return httpx.Response(200, content=stream())

    try:
        await _client(handler).download("/x", max_bytes=100)
    except ResponseTooLarge as err:
        assert isinstance(err, ApiError) and err.status is None and err.limit == 100
    else:
        raise AssertionError("expected ResponseTooLarge")


async def test_download_refuses_on_a_declared_length_before_reading():
    from slimbots.http import ResponseTooLarge

    def handler(request):
        return httpx.Response(200, content=b"x" * 50, headers={"content-length": "500"})

    try:
        await _client(handler).download("/x", max_bytes=100)
    except ResponseTooLarge:
        pass
    else:
        raise AssertionError("expected ResponseTooLarge")


async def test_download_turns_an_http_error_and_a_network_failure_into_api_errors():
    from slimbots.http import ApiError

    def missing(request):
        return httpx.Response(404, json={"error": "not found"})

    try:
        await _client(missing).download("/x")
    except ApiError as err:
        assert err.status == 404 and err.reason == "not found"

    def broken(request):
        raise httpx.ConnectError("down")

    try:
        await _client(broken).download("/x")
    except ApiError as err:
        assert err.status is None
    else:
        raise AssertionError("expected ApiError")
