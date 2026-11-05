from slimbots import BatchUnavailable, Canvas
from slimbots.http import ApiError
from slimbots.testing import FakeAsyncClient


async def test_place_posts_to_canvas_objects():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/objects", {"id": "o1", "seq": 1})
    canvas = Canvas(client, "c1")
    result = await canvas.place("note", x=0, y=0, w=220, h=140, props={"text": "hi"})
    assert result == {"id": "o1", "seq": 1}
    assert client.calls[-1][1] == "/channels/c1/canvas/objects"
    assert client.calls[-1][2]["kind"] == "note"
    assert client.calls[-1][2]["props"] == {"text": "hi"}


async def test_place_without_props_still_sends_the_required_props_field():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/objects", {"id": "o1", "seq": 1})
    await Canvas(client, "c1").place("shape", x=0, y=0, w=10, h=10)
    assert client.calls[-1][2]["props"] == {}


async def test_move_posts_a_move_op():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/ops", None)
    canvas = Canvas(client, "c1")
    await canvas.move("o1", x=10, y=20)
    body = client.calls[-1][2]
    assert body["kind"] == "move"
    assert body["object_id"] == "o1"
    assert body["x"] == 10
    assert body["y"] == 20


async def test_remove_posts_a_remove_op_with_every_id():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/ops", None)
    canvas = Canvas(client, "c1")
    await canvas.remove(["o1", "o2"])
    body = client.calls[-1][2]
    assert body["kind"] == "remove"
    assert body["object_ids"] == ["o1", "o2"]


async def test_clear_posts_a_clear_op_with_before_seq():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/ops", None)
    canvas = Canvas(client, "c1")
    await canvas.clear(42)
    body = client.calls[-1][2]
    assert body["kind"] == "clear"
    assert body["before_seq"] == 42


async def test_restore_posts_a_restore_op_naming_the_target_op():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/ops", None)
    canvas = Canvas(client, "c1")
    await canvas.restore("op-1")
    body = client.calls[-1][2]
    assert body["kind"] == "restore"
    assert body["target_op"] == "op-1"


async def test_reorder_posts_a_reorder_op_with_the_explicit_z_index():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/ops", None)
    canvas = Canvas(client, "c1")
    await canvas.reorder("o1", -5)
    body = client.calls[-1][2]
    assert body["kind"] == "reorder"
    assert body["object_id"] == "o1"
    assert body["z_index"] == -5


async def test_viewport_sends_the_rectangle_as_query_params():
    client = FakeAsyncClient()
    client.respond("GET", "/channels/c1/canvas/objects", {"objects": []})
    canvas = Canvas(client, "c1")
    await canvas.viewport(min_x=0, min_y=0, max_x=100, max_y=100)
    method, path, body, params = client.calls[-1]
    assert method == "GET"
    assert path == "/channels/c1/canvas/objects"
    assert params == {"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 100, "limit": 100}


async def test_place_many_posts_every_object_in_one_request_and_returns_their_ids():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/objects/batch", {"objects": []})
    ids = await Canvas(client, "c1").place_many(
        [{"kind": "stroke", "x": 1, "y": 2, "w": 3, "h": 4, "props": {"points": [0, 0, 3, 4]}, "id": "fixed"}, {"kind": "shape", "x": 0, "y": 0, "w": 5, "h": 5}]
    )
    method, path, body, _ = client.calls[-1]
    assert (method, path) == ("POST", "/channels/c1/canvas/objects/batch")
    assert [o["id"] for o in body["objects"]] == ids and ids[0] == "fixed" and len(ids[1]) == 36
    assert body["objects"][1]["props"] == {} and body["objects"][0]["props"] == {"points": [0, 0, 3, 4]}


async def test_place_many_refuses_more_than_the_servers_batch_limit_and_sends_nothing_for_none():
    client = FakeAsyncClient()
    assert await Canvas(client, "c1").place_many([]) == []
    try:
        await Canvas(client, "c1").place_many([{"kind": "shape", "x": 0, "y": 0, "w": 1, "h": 1}] * 201)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")


async def test_a_missing_batch_route_is_batch_unavailable_and_other_errors_pass_through():
    for status in (404, 405):
        client = FakeAsyncClient()
        client.respond("POST", "/channels/c1/canvas/objects/batch", ApiError(status, "no route"))
        try:
            await Canvas(client, "c1").place_many([{"kind": "shape", "x": 0, "y": 0, "w": 1, "h": 1}])
        except BatchUnavailable:
            pass
        else:
            raise AssertionError("expected BatchUnavailable")
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/objects/batch", ApiError(429, "slow down"))
    try:
        await Canvas(client, "c1").place_many([{"kind": "shape", "x": 0, "y": 0, "w": 1, "h": 1}])
    except ApiError as err:
        assert err.status == 429 and not isinstance(err, BatchUnavailable)


async def test_place_many_returns_the_ids_the_server_answers_with_in_request_order():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/objects/batch", {"objects": [{"id": "srv-1", "seq": 1}, {"id": "srv-2", "seq": 2}]})
    ids = await Canvas(client, "c1").place_many([{"kind": "shape", "x": 0, "y": 0, "w": 1, "h": 1}, {"kind": "shape", "x": 1, "y": 1, "w": 1, "h": 1}])
    assert ids == ["srv-1", "srv-2"]


async def test_place_many_keeps_its_own_ids_when_the_answer_does_not_list_them():
    client = FakeAsyncClient()
    client.respond("POST", "/channels/c1/canvas/objects/batch", None)
    ids = await Canvas(client, "c1").place_many([{"kind": "shape", "x": 0, "y": 0, "w": 1, "h": 1, "id": "mine"}])
    assert ids == ["mine"]
