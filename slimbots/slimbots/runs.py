"""A connector's manifest commands and panels (slim-m add-on API 7): the host forwards a member's run as a
`connector.run` frame and waits, bounded, for one answer at `POST /connectors/me/runs/{run_id}`; see docs/framework.md."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .http import ApiError

if TYPE_CHECKING:
    from .bot import Bot

log = logging.getLogger("slimbots.runs")

SCENE_TAG = "scene/1"
# Answers sent a little before the host's deadline, so a slow handler fails visibly instead of silently.
DEADLINE_MARGIN_SECONDS = 0.5
DEFAULT_DEADLINE_MS = 6000
# The host refuses a longer answer with a 400, which the member would read as a failed run with no reason.
MAX_ANSWER_BYTES = 1024 * 1024


class RunError(Exception):
    """Raise from a run handler to answer the member with this text as the error."""


@dataclass(frozen=True)
class Caller:
    """Who ran it: an id that is stable per connector but is not the member's account id, and their shown name."""

    id: str
    name: str | None = None


@dataclass(frozen=True)
class Run:
    """One forwarded run. A panel's first open has an empty `input`; a press carries `action` and the held `state`."""

    command: str
    input: str
    caller: Caller
    channel_id: str | None = None
    voice_channel_id: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)
    now_ms: int = 0
    action: str | None = None
    state: str | None = None

    @classmethod
    def from_request(cls, request: dict[str, Any]) -> Run:
        caller = request.get("caller") or {}
        context = request.get("context") or {}
        text = str(request.get("input") or "")
        action, state = _pressed(text)
        return cls(
            command=str(request.get("command") or ""), input=text,
            caller=Caller(str(caller.get("id") or ""), caller.get("name")),
            channel_id=context.get("channel_id"), voice_channel_id=context.get("voice_channel_id"),
            settings=dict(request.get("settings") or {}), now_ms=int(request.get("now_ms") or 0),
            action=action, state=state,
        )


def _pressed(text: str) -> tuple[str | None, str | None]:
    """The `{"action", "state"}` a scene press sends, or nothing for plain input."""
    if not text.startswith("{"):
        return None, None
    try:
        asked = json.loads(text)
    except ValueError:
        return None, None
    if not isinstance(asked, dict) or not isinstance(asked.get("action"), str):
        return None, None
    state = asked.get("state")
    return asked["action"], state if isinstance(state, str) else None


def scene(width: int, height: int, ops: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    """A `scene/1` document; `extra` adds `status`, `controls`, `state`, `refresh_ms` and the rest."""
    return {"$slim": SCENE_TAG, "width": width, "height": height, "ops": ops, **extra}


RunHandler = Callable[[Run], Awaitable[Any]]


class RunRoutes:
    """Manifest command name to its handler."""

    def __init__(self) -> None:
        self._handlers: dict[str, RunHandler] = {}

    def add(self, name: str, func: RunHandler) -> None:
        if name in self._handlers:
            raise ValueError(f"manifest command {name!r} has two handlers")
        self._handlers[name] = func

    def get(self, name: str) -> RunHandler | None:
        return self._handlers.get(name)

    def __bool__(self) -> bool:
        return bool(self._handlers)


def answer_of(result: Any) -> dict[str, Any]:
    """A handler's return as the module answer: text as is, a scene dict as its JSON, None as empty text."""
    if result is None:
        return {"ok": True, "output": ""}
    if isinstance(result, str):
        return {"ok": True, "output": result}
    if isinstance(result, dict) and result.get("$slim") == SCENE_TAG:
        return {"ok": True, "output": json.dumps(result, separators=(",", ":"))}
    raise TypeError(f"a run handler returns text, a scene or None, not {type(result).__name__}")


async def dispatch_run(bot: Bot, routes: RunRoutes, frame: dict[str, Any]) -> None:
    """Runs the handler for one `connector.run` frame and posts its answer, or why it has none."""
    run_id = frame.get("run_id")
    request = frame.get("request")
    if not isinstance(run_id, str) or not isinstance(request, dict):
        log.warning("skipped a malformed connector.run frame")
        return
    run = Run.from_request(request)
    handler = routes.get(run.command)
    deadline = int(frame.get("deadline_ms") or DEFAULT_DEADLINE_MS) / 1000 - DEADLINE_MARGIN_SECONDS
    if handler is None:
        answer = {"ok": False, "error": f"this connector has no command {run.command!r}"}
    else:
        answer = await _answer(handler, run, max(deadline, 0.1))
    await post_answer(bot, run_id, answer)


async def _answer(handler: RunHandler, run: Run, deadline: float) -> dict[str, Any]:
    try:
        answer = answer_of(await asyncio.wait_for(handler(run), timeout=deadline))
    except RunError as err:
        return {"ok": False, "error": str(err) or "the connector refused this run"}
    except asyncio.TimeoutError:
        return {"ok": False, "error": "the connector took too long to answer"}
    except Exception:
        log.exception("manifest command %r failed", run.command)
        return {"ok": False, "error": "the connector failed to answer"}
    if len(answer["output"].encode()) > MAX_ANSWER_BYTES:
        log.warning("manifest command %r answered more than %d bytes", run.command, MAX_ANSWER_BYTES)
        return {"ok": False, "error": "the connector's answer was too large"}
    return answer


async def post_answer(bot: Bot, run_id: str, answer: dict[str, Any]) -> None:
    """One try: a run the host stopped waiting for answers 404, and a retry could only be later still."""
    assert bot.client is not None
    try:
        await bot.client.call("POST", f"/connectors/me/runs/{run_id}", answer, retries=0)
    except ApiError as err:
        if err.status == 404:
            log.info("a run was answered after the host stopped waiting")
            return
        log.warning("the host refused a run's answer (%s)", err.status)
