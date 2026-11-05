"""A channel's Voice Canvas: place/move/remove an object, and read a viewport; see docs/framework.md."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from .bot import Bot
    from .http import AsyncClient


MAX_BATCH = 200  # the server's most objects in one batch request


class BatchUnavailable(Exception):
    """The server has no batch placement route (an older deployment); place one object at a time instead."""


class Canvas:
    """Thin wrapper over one channel's `canvas/objects`/`canvas/ops` REST routes and its two live gateway signals."""

    def __init__(self, client: AsyncClient, channel_id: str, *, bot: Bot | None = None) -> None:
        self._client = client
        self.channel_id = channel_id
        self._bot = bot

    async def place(
        self, kind: str, *, x: float, y: float, w: float, h: float,
        props: dict[str, Any] | None = None, object_id: str | None = None,
    ) -> Any:
        body: dict[str, Any] = {"id": object_id or str(uuid.uuid4()), "kind": kind, "x": x, "y": y, "w": w, "h": h, "props": props or {}}
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/objects", body)

    async def place_many(self, objects: Iterable[dict[str, Any]]) -> list[str]:
        """Places up to MAX_BATCH objects (`{kind, x, y, w, h, props?, id?}`) in one request, returns the ids the server answers with; `BatchUnavailable` on 404/405."""
        from .http import ApiError

        bodies = [
            {"id": o.get("id") or str(uuid.uuid4()), "kind": o["kind"], "x": o["x"], "y": o["y"], "w": o["w"], "h": o["h"], "props": o.get("props") or {}}
            for o in objects
        ]
        if not bodies:
            return []
        if len(bodies) > MAX_BATCH:
            raise ValueError(f"place_many takes at most {MAX_BATCH} objects, got {len(bodies)}")
        try:
            response = await self._client.call("POST", f"/channels/{self.channel_id}/canvas/objects/batch", {"objects": bodies})
        except ApiError as err:
            if err.status in (404, 405):
                raise BatchUnavailable(f"HTTP {err.status}") from err
            raise
        placed = response.get("objects") if isinstance(response, dict) else None
        if isinstance(placed, list) and len(placed) == len(bodies) and all(isinstance(o, dict) and "id" in o for o in placed):
            return [o["id"] for o in placed]
        return [body["id"] for body in bodies]

    async def move(
        self, object_id: str, *, x: float, y: float, w: float | None = None, h: float | None = None,
        op_id: str | None = None,
    ) -> Any:
        body: dict[str, Any] = {"id": op_id or str(uuid.uuid4()), "kind": "move", "object_id": object_id, "x": x, "y": y}
        if w is not None:
            body["w"] = w
        if h is not None:
            body["h"] = h
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/ops", body)

    async def remove(self, object_ids: Iterable[str], *, op_id: str | None = None) -> Any:
        body = {"id": op_id or str(uuid.uuid4()), "kind": "remove", "object_ids": list(object_ids)}
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/ops", body)

    async def clear(self, before_seq: int, *, op_id: str | None = None) -> Any:
        """Removes every live object placed at or below `before_seq`; needs MANAGE_CANVAS unconditionally."""
        body = {"id": op_id or str(uuid.uuid4()), "kind": "clear", "before_seq": before_seq}
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/ops", body)

    async def restore(self, target_op: str, *, op_id: str | None = None) -> Any:
        """Un-deletes exactly what a prior `remove` or `clear` op (`target_op`, its id) touched."""
        body = {"id": op_id or str(uuid.uuid4()), "kind": "restore", "target_op": target_op}
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/ops", body)

    async def reorder(self, object_id: str, z_index: int, *, op_id: str | None = None) -> Any:
        """Sets one live object's stacking order to an explicit `z_index` - the caller computes the target."""
        body = {"id": op_id or str(uuid.uuid4()), "kind": "reorder", "object_id": object_id, "z_index": z_index}
        return await self._client.call("POST", f"/channels/{self.channel_id}/canvas/ops", body)

    async def viewport(self, *, min_x: float, min_y: float, max_x: float, max_y: float, limit: int = 100) -> Any:
        """One page of objects in a rectangle - a bot's own reconciliation ground truth; see docs/framework.md."""
        params = {"min_x": min_x, "min_y": min_y, "max_x": max_x, "max_y": max_y, "limit": limit}
        return await self._client.call("GET", f"/channels/{self.channel_id}/canvas/objects", params=params)

    async def send_cursor(self, x: float, y: float) -> None:
        """A live pointer position over the gateway; no ack and no history, it just stops arriving when you stop."""
        await self._bot_or_raise().send_frame({"type": "canvas.cursor", "channel_id": self.channel_id, "x": x, "y": y})

    async def send_stroke_preview(self, object_id: str, points: Iterable[Any], *, ended: bool = False) -> None:
        """An in-flight drawing stroke over the gateway; send it repeatedly as it grows, `ended=True` on the last one."""
        await self._bot_or_raise().send_frame({
            "type": "canvas.stroke_preview", "channel_id": self.channel_id,
            "object_id": object_id, "points": list(points), "ended": ended,
        })

    def _bot_or_raise(self) -> Bot:
        if self._bot is None:
            raise RuntimeError("Canvas(..., bot=...) needs a bot to send a live gateway signal")
        return self._bot
